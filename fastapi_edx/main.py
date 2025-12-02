from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uuid
import requests
import os
import logging
import re
from urllib.parse import urljoin
from dotenv import load_dotenv
from pydantic import EmailStr, validator

from models import UserData
from db import SessionLocal, UserLink, UserToken, engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Open edX Auto-Login Service")

# Add CORS middleware to allow iframe embedding
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for iframe embedding
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add middleware to set proper headers for iframe embedding
@app.middleware("http")
async def add_iframe_headers(request: Request, call_next):
    response = await call_next(request)
    
    # Allow iframe embedding from any origin
    response.headers["X-Frame-Options"] = "ALLOWALL"
    response.headers["Content-Security-Policy"] = "frame-ancestors *"
    
    # Remove any restrictive headers that might block iframe embedding
    if "X-Content-Type-Options" in response.headers:
        del response.headers["X-Content-Type-Options"]
    
    return response

# Setup templates
templates = Jinja2Templates(directory="templates")

# Mount static files (if needed)
# app.mount("/static", StaticFiles(directory="static"), name="static")

# Open edX Config
FASTAPI_PUBLIC_BASE_URL = os.getenv("FASTAPI_PUBLIC_BASE_URL", "http://localhost:8000")
OPENEDX_API_BASE = os.getenv("OPENEDX_API_BASE", "http://localhost:18000").rstrip('/')
COURSE_ID = os.getenv("COURSE_ID", "course-v1:Example+Demo+2025")
# Normalize dashboard URL - remove trailing slash from base and add /dashboard
OPENEDX_DASHBOARD_URL = os.getenv("OPENEDX_DASHBOARD_URL", "").rstrip('/')
if not OPENEDX_DASHBOARD_URL:
    OPENEDX_DASHBOARD_URL = f"{OPENEDX_API_BASE}/dashboard"
DEFAULT_USER_PASSWORD = os.getenv("DEFAULT_USER_PASSWORD", "ChangeMe!2345")

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Helper function to generate valid Open edX username from email
def generate_username_from_email(email: str) -> str:
    """Generate a valid Open edX username from email address.
    Open edX usernames can only contain letters (A-Z, a-z), numerals (0-9), underscores (_), and hyphens (-).
    """
    username = email.split("@")[0].replace(".", "_").replace("+", "_").replace("-", "_")
    # Remove any invalid characters and ensure it starts with a letter
    username = re.sub(r'[^a-zA-Z0-9_-]', '', username)
    if username and not username[0].isalpha():
        username = "user_" + username
    return username

# Helper function to attempt password reset for existing users
def attempt_password_reset(session, email: str, new_password: str, csrf_token: str, openedx_base: str) -> bool:
    """Attempt to reset password for an existing user"""
    try:
        # Try to use the password reset API if available
        reset_data = {
            "email": email,
            "new_password": new_password,
            "confirm_password": new_password
        }
        
        if csrf_token:
            reset_data["csrfmiddlewaretoken"] = csrf_token
        
        headers = {
            "Referer": f"{openedx_base}/login",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
        
        # Try different password reset endpoints
        reset_endpoints = [
            "/user_api/v1/account/password_reset/",
            "/password_reset/",
            "/user_api/v1/account/change_password/"
        ]
        
        for endpoint in reset_endpoints:
            try:
                reset_response = session.post(
                    f"{openedx_base}{endpoint}",
                    data=reset_data,
                    headers=headers,
                    timeout=15
                )
                logger.info(f"Password reset attempt at {endpoint}: {reset_response.status_code}")
                if reset_response.status_code in [200, 201, 204]:
                    return True
            except:
                continue
        
        return False
        
    except Exception as e:
        logger.error(f"Password reset failed: {str(e)}")
        return False

# Serve the main HTML form
@app.get("/", response_class=HTMLResponse)
def serve_form(request: Request):
    """Serve the main HTML form for user input"""
    return templates.TemplateResponse("index.html", {"request": request})

# Configuration validation endpoint
@app.get("/config-check")
def config_check():
    """Check if Open edX configuration is properly set up"""
    config_status = {
        "fastapi_base_url": FASTAPI_PUBLIC_BASE_URL,
        "openedx_api_base": OPENEDX_API_BASE,
        "course_id": COURSE_ID,
        "dashboard_url": OPENEDX_DASHBOARD_URL,
        "database_url": os.getenv("DATABASE_URL", "sqlite:///./fastapi_edx.db"),
        "authentication_method": "Direct form-based (no OAuth required)",
        "issues": [],
        "recommendations": []
    }
    
    # Check for placeholder values
    if OPENEDX_API_BASE == "https://your-openedx-domain.com":
        config_status["issues"].append("OPENEDX_API_BASE is using placeholder value")
        config_status["recommendations"].append("Set OPENEDX_API_BASE environment variable to your Open edX URL (e.g., http://localhost:18000)")
    
    # Check if dashboard URL is properly constructed
    if not OPENEDX_DASHBOARD_URL or OPENEDX_DASHBOARD_URL == "":
        config_status["issues"].append("OPENEDX_DASHBOARD_URL is empty")
        config_status["recommendations"].append("Set OPENEDX_DASHBOARD_URL environment variable or ensure OPENEDX_API_BASE is set")
    
    # Check if URLs are valid
    try:
        from urllib.parse import urlparse
        parsed = urlparse(OPENEDX_API_BASE)
        if not parsed.scheme or not parsed.netloc:
            config_status["issues"].append("OPENEDX_API_BASE is not a valid URL")
    except:
        config_status["issues"].append("OPENEDX_API_BASE is not a valid URL")
    
    return config_status

# Test Open edX connectivity
@app.get("/test-openedx")
def test_openedx():
    """Test connectivity to Open edX platform"""
    try:
        # Test basic connectivity
        response = requests.get(f"{OPENEDX_API_BASE}/", timeout=10)
        connectivity_status = {
            "openedx_url": OPENEDX_API_BASE,
            "connectivity": "OK" if response.status_code == 200 else f"HTTP {response.status_code}",
            "response_time": response.elapsed.total_seconds(),
            "issues": []
        }
        
        # Test API endpoint
        try:
            api_response = requests.get(f"{OPENEDX_API_BASE}/user_api/v1/accounts/", timeout=10)
            connectivity_status["api_endpoint"] = f"HTTP {api_response.status_code}"
        except requests.exceptions.RequestException as e:
            connectivity_status["api_endpoint"] = f"Error: {str(e)}"
            connectivity_status["issues"].append("API endpoint not accessible")
            
        return connectivity_status
        
    except requests.exceptions.RequestException as e:
        return {
            "openedx_url": OPENEDX_API_BASE,
            "connectivity": f"Error: {str(e)}",
            "issues": ["Cannot connect to Open edX platform"]
        }

# Generate single persistent link
@app.post("/generate-link")
def generate_link(user: UserData, request: Request, db: Session = Depends(get_db)):
    # Check if link exists
    existing_link = db.query(UserLink).filter(UserLink.email == user.email).first()
    if existing_link:
        link_url = f"{FASTAPI_PUBLIC_BASE_URL}/access/{existing_link.link_id}"
    else:
        # Create new link
        link_id = str(uuid.uuid4())
        new_link = UserLink(link_id=link_id, email=user.email)
        db.add(new_link)
        db.commit()
        link_url = f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}"
    
    # Check if request is from the HTML form (has Accept: text/html header)
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        # Return HTML response with iframe
        iframe_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Open edX Dashboard</title>
            <style>
                body {{
                    margin: 0;
                    padding: 20px;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: #f5f5f5;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 20px;
                    padding: 20px;
                    background: white;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .iframe-container {{
                    background: white;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    overflow: hidden;
                }}
                iframe {{
                    width: 100%;
                    height: 80vh;
                    border: none;
                }}
                .loading {{
                    text-align: center;
                    padding: 50px;
                    color: #666;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🎓 Welcome to Open edX Dashboard</h1>
                <p>Loading your personalized learning dashboard...</p>
            </div>
            <div class="iframe-container">
                <div class="loading">Redirecting to Open edX dashboard...</div>
                <iframe src="{link_url}" title="Open edX Dashboard" onload="this.style.display='block'; this.previousElementSibling.style.display='none';"></iframe>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=iframe_html)
    else:
        # Return JSON response for API calls
        return {"link": link_url}

# Proxy endpoint to serve Open edX dashboard with proper session handling
@app.get("/dashboard-proxy/{link_id}")
def dashboard_proxy(link_id: str, request: Request, db: Session = Depends(get_db)):
    """Proxy endpoint that serves Open edX dashboard with proper session cookies"""
    user_link = db.query(UserLink).filter(UserLink.link_id == link_id).first()
    if not user_link:
        raise HTTPException(status_code=404, detail="Invalid link")

    email = user_link.email
    user_token = db.query(UserToken).filter(UserToken.email == email).first()
    
    # If no valid session found, redirect to access endpoint to create one
    if not user_token or not user_token.access_token:
        logger.info(f"No valid session found for user {email}, redirecting to access endpoint")
        return RedirectResponse(url=f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}?format=redirect", status_code=307)

    # Create a session with the stored cookies
    session = requests.Session()
    session.cookies.set("lms_sessionid", user_token.access_token)
    session.cookies.set("sessionid", user_token.access_token)
    
    try:
        # Validate and normalize dashboard URL
        if not OPENEDX_DASHBOARD_URL or OPENEDX_DASHBOARD_URL == "http://localhost:18000/dashboard":
            logger.warning(f"Dashboard URL not properly configured: {OPENEDX_DASHBOARD_URL}")
            # Try to construct a valid URL (ensure no double slashes)
            if OPENEDX_API_BASE and OPENEDX_API_BASE != "https://your-openedx-domain.com":
                base = OPENEDX_API_BASE.rstrip('/')
                dashboard_url = f"{base}/dashboard"
            else:
                raise HTTPException(status_code=500, detail="Open edX configuration not set. Please set OPENEDX_API_BASE environment variable.")
        else:
            dashboard_url = OPENEDX_DASHBOARD_URL.rstrip('/')
            # Ensure it ends with /dashboard if it's just the base URL
            if not dashboard_url.endswith('/dashboard'):
                if dashboard_url == OPENEDX_API_BASE.rstrip('/'):
                    dashboard_url = f"{dashboard_url}/dashboard"
            
        logger.info(f"Fetching dashboard from: {dashboard_url}")
        
        # Fetch the dashboard content with the session
        dashboard_response = session.get(dashboard_url, timeout=30)
        
        if dashboard_response.status_code == 200:
            # Process the HTML content to fix relative URLs and navigation
            dashboard_content = dashboard_response.text
            
            # Replace relative URLs with our proxy URLs to maintain session
            # Handle static assets (CSS, JS, images) with static proxy
            dashboard_content = dashboard_content.replace('src="/static/', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            dashboard_content = dashboard_content.replace('href="/static/', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            dashboard_content = dashboard_content.replace('url("/static/', f'url("{FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            dashboard_content = dashboard_content.replace('url(/static/', f'url({FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            
            # Handle other relative URLs with navigation proxy
            dashboard_content = dashboard_content.replace('src="/', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/')
            dashboard_content = dashboard_content.replace('href="/', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/')
            dashboard_content = dashboard_content.replace('action="/', f'action="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/')
            
            # Add base tag to ensure relative URLs work correctly
            if '<head>' in dashboard_content:
                dashboard_content = dashboard_content.replace(
                    '<head>', 
                    f'<head><base href="{OPENEDX_API_BASE}/">'
                )
            else:
                # If no head tag, add it
                dashboard_content = f'<head><base href="{OPENEDX_API_BASE}/"></head>{dashboard_content}'
            
            # Create HTML response with the dashboard content
            dashboard_html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Open edX Dashboard</title>
                <style>
                    body {{
                        margin: 0;
                        padding: 0;
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background: #f5f5f5;
                    }}
                    .header {{
                        background: white;
                        padding: 20px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        margin-bottom: 20px;
                        position: sticky;
                        top: 0;
                        z-index: 1000;
                    }}
                    .header h1 {{
                        margin: 0;
                        color: #333;
                        font-size: 1.5em;
                    }}
                    .dashboard-content {{
                        background: white;
                        margin: 0 20px 20px 20px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        overflow: hidden;
                        min-height: 80vh;
                    }}
                    .loading {{
                        text-align: center;
                        padding: 50px;
                        color: #666;
                    }}
                </style>
            </head>
            <body>
                <div class="header">
                    <h1>🎓 Open edX Dashboard - {email}</h1>
                </div>
                <div class="dashboard-content">
                    {dashboard_content}
                </div>
                
                <script>
                    // Simple navigation handler - links are already proxied
                    document.addEventListener('DOMContentLoaded', function() {{
                        console.log('Dashboard loaded successfully for user: {email}');
                        
                        // Set session cookies for the current domain
                        document.cookie = "lms_sessionid={user_token.access_token}; path=/; SameSite=Lax";
                        document.cookie = "sessionid={user_token.access_token}; path=/; SameSite=Lax";
                        
                        // Handle any remaining navigation issues
                        document.addEventListener('click', function(e) {{
                            const link = e.target.closest('a');
                            if (link && link.href) {{
                                // If it's a direct Open edX URL (not proxied), convert it
                                if (link.href.includes('{OPENEDX_API_BASE}') && !link.href.includes('/openedx-proxy/')) {{
                                    e.preventDefault();
                                    const path = link.href.replace('{OPENEDX_API_BASE}/', '');
                                    window.location.href = '{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/' + path;
                                }}
                            }}
                        }});
                    }});
                </script>
            </body>
            </html>
            """
            
            response = HTMLResponse(content=dashboard_html)
            # Allow iframe embedding from any origin (including localhost)
            response.headers["X-Frame-Options"] = "ALLOWALL"
            response.headers["Content-Security-Policy"] = "frame-ancestors *"
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "*"
            
            # Set link_id cookie for navigation tracking
            response.set_cookie(
                key="edx_link_id",
                value=link_id,
                path="/",
                httponly=False,  # Allow JavaScript to read it if needed
                samesite="lax",
                max_age=86400  # 24 hours
            )
            
            return response
        else:
            # If dashboard fetch fails, return a helpful error page
            error_html = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Dashboard Error</title>
                <style>
                    body {{
                        margin: 0;
                        padding: 20px;
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background: #f5f5f5;
                    }}
                    .error-container {{
                        max-width: 600px;
                        margin: 50px auto;
                        background: white;
                        padding: 30px;
                        border-radius: 10px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        text-align: center;
                    }}
                    .error-icon {{
                        font-size: 4em;
                        color: #e74c3c;
                        margin-bottom: 20px;
                    }}
                    .error-title {{
                        color: #333;
                        font-size: 1.5em;
                        margin-bottom: 15px;
                    }}
                    .error-message {{
                        color: #666;
                        margin-bottom: 20px;
                        line-height: 1.6;
                    }}
                    .error-details {{
                        background: #f8f9fa;
                        padding: 15px;
                        border-radius: 5px;
                        margin: 20px 0;
                        text-align: left;
                        font-family: monospace;
                        font-size: 0.9em;
                    }}
                    .retry-btn {{
                        background: #3498db;
                        color: white;
                        padding: 12px 24px;
                        border: none;
                        border-radius: 5px;
                        cursor: pointer;
                        font-size: 1em;
                        margin-top: 20px;
                    }}
                    .retry-btn:hover {{
                        background: #2980b9;
                    }}
                </style>
            </head>
            <body>
                <div class="error-container">
                    <div class="error-icon">⚠️</div>
                    <h1 class="error-title">Dashboard Access Error</h1>
                    <p class="error-message">
                        Unable to load the Open edX dashboard. This could be due to configuration issues or network problems.
                    </p>
                    <div class="error-details">
                        <strong>Error Details:</strong><br>
                        Status Code: {dashboard_response.status_code}<br>
                        Dashboard URL: {dashboard_url}<br>
                        User: {email}
                    </div>
                    <button class="retry-btn" onclick="window.location.reload()">🔄 Retry</button>
                    <p style="margin-top: 20px; font-size: 0.9em; color: #999;">
                        If this problem persists, please check your Open edX configuration.
                    </p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html)
            
    except requests.exceptions.RequestException as e:
        # Return a more helpful error page
        error_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Dashboard Error</title>
            <style>
                body {{
                    margin: 0;
                    padding: 20px;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: #f5f5f5;
                }}
                .error-container {{
                    max-width: 600px;
                    margin: 50px auto;
                    background: white;
                    padding: 30px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    text-align: center;
                }}
                .error-icon {{
                    font-size: 4em;
                    color: #e74c3c;
                    margin-bottom: 20px;
                }}
                .error-title {{
                    color: #333;
                    font-size: 1.5em;
                    margin-bottom: 15px;
                }}
                .error-message {{
                    color: #666;
                    margin-bottom: 20px;
                    line-height: 1.6;
                }}
                .error-details {{
                    background: #f8f9fa;
                    padding: 15px;
                    border-radius: 5px;
                    margin: 20px 0;
                    text-align: left;
                    font-family: monospace;
                    font-size: 0.9em;
                }}
                .retry-btn {{
                    background: #3498db;
                    color: white;
                    padding: 12px 24px;
                    border: none;
                    border-radius: 5px;
                    cursor: pointer;
                    font-size: 1em;
                    margin-top: 20px;
                }}
                .retry-btn:hover {{
                    background: #2980b9;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">🔧</div>
                <h1 class="error-title">Configuration Error</h1>
                <p class="error-message">
                    There's an issue with the Open edX configuration. Please check your environment variables.
                </p>
                <div class="error-details">
                    <strong>Error Details:</strong><br>
                    {str(e)}<br>
                    Dashboard URL: {OPENEDX_DASHBOARD_URL}<br>
                    Open edX Base: {OPENEDX_API_BASE}<br>
                    User: {email}
                </div>
                <button class="retry-btn" onclick="window.location.reload()">🔄 Retry</button>
                <p style="margin-top: 20px; font-size: 0.9em; color: #999;">
                    Please check your .env file or environment variables for proper Open edX configuration.
                </p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=error_html)

# Static assets proxy endpoint (no authentication required)
@app.get("/openedx-static/{path:path}")
def openedx_static_proxy(path: str, request: Request):
    """Proxy endpoint for static assets (CSS, JS, images) - no authentication required"""
    # Construct the full Open edX URL for static assets
    openedx_url = f"{OPENEDX_API_BASE}/{path}"
    
    # Add query parameters if present
    if request.query_params:
        openedx_url += "?" + str(request.query_params)
    
    try:
        # Fetch the static asset directly from Open edX
        response = requests.get(openedx_url, timeout=30)
        
        if response.status_code == 200:
            # Return the asset with proper content type
            content_type = response.headers.get('content-type', 'application/octet-stream')
            return Response(
                content=response.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*"
                }
            )
        else:
            raise HTTPException(status_code=response.status_code, detail="Static asset not found")
            
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Static asset request failed: {str(e)}")

# Navigation proxy endpoint to handle all Open edX requests within the iframe
@app.get("/openedx-proxy/{path:path}")
def openedx_proxy(path: str, request: Request, db: Session = Depends(get_db)):
    """Proxy endpoint to handle navigation within Open edX"""
    # For static assets, redirect to static proxy
    if path.startswith('static/'):
        return RedirectResponse(url=f"{FASTAPI_PUBLIC_BASE_URL}/openedx-static/{path}", status_code=307)
    
    # Extract link_id from multiple sources
    referer = request.headers.get("referer", "")
    link_id = None
    
    # Strategy 1: Try to extract link_id from referer URL (multiple patterns)
    if referer:
        # Check for /dashboard-proxy/{link_id}
        if "/dashboard-proxy/" in referer:
            link_id = referer.split("/dashboard-proxy/")[1].split("?")[0].split("#")[0].split("/")[0]
        # Check for /access/{link_id}
        elif "/access/" in referer:
            link_id = referer.split("/access/")[1].split("?")[0].split("#")[0].split("/")[0]
        # Check for /openedx-proxy/ (might have link_id in query or previous path)
        elif "/openedx-proxy/" in referer:
            # Try to extract from query parameter
            if "link_id=" in referer:
                link_id = referer.split("link_id=")[1].split("&")[0].split("#")[0]
    
    # Strategy 2: Try to get link_id from cookies
    if not link_id:
        link_id = request.cookies.get("edx_link_id")
    
    # Strategy 3: Try to get link_id from query parameters
    if not link_id:
        link_id = request.query_params.get("link_id")
    
    # Strategy 4: Try to find link_id from session cookie (if we have user email)
    if not link_id:
        session_cookie = request.cookies.get("lms_sessionid") or request.cookies.get("sessionid")
        if session_cookie:
            # Try to find user token by session cookie
            user_token = db.query(UserToken).filter(UserToken.access_token == session_cookie).first()
            if user_token:
                # Find link for this user
                user_link = db.query(UserLink).filter(UserLink.email == user_token.email).first()
                if user_link:
                    link_id = user_link.link_id
    
    if not link_id:
        logger.warning(f"Could not extract link_id from referer: {referer}, cookies: {dict(request.cookies)}")
        raise HTTPException(status_code=400, detail="Invalid navigation request - link_id not found")
    
    # Get user session
    user_link = db.query(UserLink).filter(UserLink.link_id == link_id).first()
    if not user_link:
        raise HTTPException(status_code=404, detail="Invalid link")
    
    user_token = db.query(UserToken).filter(UserToken.email == user_link.email).first()
    if not user_token or not user_token.access_token:
        raise HTTPException(status_code=400, detail="No valid session found")
    
    # Construct the full Open edX URL
    openedx_url = f"{OPENEDX_API_BASE}/{path}"
    
    # Add query parameters if present
    if request.query_params:
        openedx_url += "?" + str(request.query_params)
    
    # Create session with stored cookies
    session = requests.Session()
    session.cookies.set("lms_sessionid", user_token.access_token)
    session.cookies.set("sessionid", user_token.access_token)
    
    try:
        # Fetch the Open edX page
        response = session.get(openedx_url, timeout=30)
        
        if response.status_code == 200:
            # Process the content
            content = response.text
            
            # Replace relative URLs with our proxy URLs
            # Note: link_id will be available via cookie, so we don't need to add it to every URL
            content = content.replace('href="/', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/')
            content = content.replace('src="/', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static/')
            content = content.replace('action="/', f'action="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/')
            
            # Add base tag
            if '<head>' in content:
                content = content.replace('<head>', f'<head><base href="{OPENEDX_API_BASE}/">')
            
            # Create response with link_id cookie (this is the key for navigation)
            html_response = HTMLResponse(content=content)
            html_response.set_cookie(
                key="edx_link_id",
                value=link_id,
                path="/",
                httponly=False,
                samesite="lax",
                max_age=86400
            )
            
            return html_response
        else:
            raise HTTPException(status_code=response.status_code, detail="Open edX request failed")
            
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Proxy request failed: {str(e)}")

# Access link - register/login & return JSON
@app.get("/access/{link_id}")
def access_link(link_id: str, format: str = "redirect", iframe: str = None, embedded: str = None, request: Request = None, db: Session = Depends(get_db)):
    user_link = db.query(UserLink).filter(UserLink.link_id == link_id).first()
    if not user_link:
        raise HTTPException(status_code=404, detail="Invalid link")

    email = user_link.email
    user_token = db.query(UserToken).filter(UserToken.email == email).first()

    # Always try to register and login (handles both new and existing users)
    password = DEFAULT_USER_PASSWORD
    logger.info(f"🔄 Processing user: {email} with default password: {password}")

    # Create a session to handle cookies and CSRF tokens
    session = requests.Session()
    session.headers.update({"User-Agent": "fastapi-edx-bridge/1.0"})
    
    try:
        # Step 1: Try to register user (will handle existing users gracefully)
        logger.info(f"📝 Step 1: Attempting to register user: {email}")
        
        # Get CSRF token from registration page
        reg_page_response = session.get(f"{OPENEDX_API_BASE}/register", timeout=30)
        csrf_token = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
        
        # Generate a valid username from email
        username = generate_username_from_email(email)
        logger.info(f"Generated username for {email}: {username}")
        
        # Prepare registration form data
        reg_data = {
            "email": email,
            "password": password,
            "username": username,
            "name": email.split("@")[0],
            "terms_of_service": "true",
            "honor_code": "true"
        }
        
        headers = {
            "Referer": f"{OPENEDX_API_BASE}/register",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            reg_data["csrfmiddlewaretoken"] = csrf_token
        
        # Submit registration form
        reg_response = session.post(
            f"{OPENEDX_API_BASE}/user_api/v1/account/registration/",
            data=reg_data,
            headers=headers,
            timeout=30
        )
        
        logger.info(f"Registration response status: {reg_response.status_code}")
        logger.info(f"Registration response: {reg_response.text}")
        
        # Handle registration response (409 = user already exists, which is OK)
        if reg_response.status_code in [200, 201]:
            logger.info(f"✅ User {email} registered successfully")
        elif reg_response.status_code == 409:
            logger.info(f"✅ User {email} already exists, proceeding to login")
        elif reg_response.status_code == 400:
            # Check if it's a validation error we can handle
            try:
                error_data = reg_response.json()
                if "username" in error_data and "already exists" in str(error_data):
                    logger.info(f"✅ User {email} already exists (username conflict), proceeding to login")
                else:
                    logger.warning(f"Registration validation error, but proceeding to login: {reg_response.text}")
            except:
                logger.warning(f"Registration failed, but proceeding to login: {reg_response.text}")
        else:
            logger.warning(f"Registration failed with status {reg_response.status_code}, but proceeding to login")

        # Step 2: Login user (this is the key step)
        logger.info(f"🔐 Step 2: Attempting to login user: {email}")
        
        # Get fresh CSRF token from login page
        login_page_response = session.get(f"{OPENEDX_API_BASE}/login", timeout=30)
        csrf_token = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
        
        # Try multiple login strategies
        login_success = False
        session_cookie = None
        
        # Strategy 1: Username + password
        login_data = {
            "email": email,
            "password": password,
            "username": username
        }
        
        headers = {
            "Referer": f"{OPENEDX_API_BASE}/login",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            login_data["csrfmiddlewaretoken"] = csrf_token
        
        # Try API login first
        login_response = session.post(
            f"{OPENEDX_API_BASE}/user_api/v1/account/login_session/",
            data=login_data,
            headers=headers,
            timeout=30
        )
        
        logger.info(f"API Login response status: {login_response.status_code}")
        logger.info(f"API Login response: {login_response.text}")
        
        if login_response.status_code in [200, 204]:
            login_success = True
            # Extract session cookie from the session - try multiple cookie names
            session_cookie = (session.cookies.get("lms_sessionid") or 
                             session.cookies.get("sessionid") or 
                             session.cookies.get("edxsessionid") or 
                             session.cookies.get("session") or
                             session.cookies.get("edx_session"))
            logger.info(f"✅ API Login successful for {email}")
            logger.info(f"All cookies: {dict(session.cookies)}")
            logger.info(f"Session cookie extracted: {session_cookie}")
        else:
            # Strategy 2: Traditional login form
            login_response = session.post(
                f"{OPENEDX_API_BASE}/login_ajax",
                data=login_data,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"Traditional login response status: {login_response.status_code}")
            logger.info(f"Traditional login response: {login_response.text}")
            
            if login_response.status_code == 200:
                login_success = True
                session_cookie = (session.cookies.get("lms_sessionid") or 
                                 session.cookies.get("sessionid") or 
                                 session.cookies.get("edxsessionid") or 
                                 session.cookies.get("session") or
                                 session.cookies.get("edx_session"))
                logger.info(f"✅ Traditional login successful for {email}")
                logger.info(f"All cookies: {dict(session.cookies)}")
                logger.info(f"Session cookie extracted: {session_cookie}")
            else:
                # Strategy 3: Email only (without username)
                login_data_email_only = {
                    "email": email,
                    "password": password
                }
                if csrf_token:
                    login_data_email_only["csrfmiddlewaretoken"] = csrf_token
                
                login_response = session.post(
                    f"{OPENEDX_API_BASE}/login_ajax",
                    data=login_data_email_only,
                    headers=headers,
                    timeout=30
                )
                
                logger.info(f"Email-only login response status: {login_response.status_code}")
                logger.info(f"Email-only login response: {login_response.text}")
                
                if login_response.status_code == 200:
                    login_success = True
                    session_cookie = (session.cookies.get("lms_sessionid") or 
                                     session.cookies.get("sessionid") or 
                                     session.cookies.get("edxsessionid") or 
                                     session.cookies.get("session") or
                                     session.cookies.get("edx_session"))
                    logger.info(f"✅ Email-only login successful for {email}")
                    logger.info(f"All cookies: {dict(session.cookies)}")
                    logger.info(f"Session cookie extracted: {session_cookie}")
        
        if not login_success:
            # If all login attempts fail, try with common passwords
            logger.info(f"Standard login failed, trying common passwords for {email}")
            common_passwords = ["password123", "Password123", "123456", "admin123", "test123", "user123", "demo123"]
            
            for common_password in common_passwords:
                logger.info(f"Trying password: {common_password}")
                
                login_data_common = {
                    "email": email,
                    "password": common_password,
                    "username": username
                }
                if csrf_token:
                    login_data_common["csrfmiddlewaretoken"] = csrf_token
                
                login_response = session.post(
                    f"{OPENEDX_API_BASE}/login_ajax",
                    data=login_data_common,
                    headers=headers,
                    timeout=30
                )
                
                if login_response.status_code == 200:
                    login_success = True
                    session_cookie = (session.cookies.get("lms_sessionid") or 
                                     session.cookies.get("sessionid") or 
                                     session.cookies.get("edxsessionid") or 
                                     session.cookies.get("session") or
                                     session.cookies.get("edx_session"))
                    logger.info(f"✅ Login successful with password: {common_password}")
                    logger.info(f"All cookies: {dict(session.cookies)}")
                    logger.info(f"Session cookie extracted: {session_cookie}")
                    password = common_password  # Update password for storage
                    break
        
        if not login_success:
            error_detail = f"All login attempts failed for user '{email}'. User may exist with a different password."
            logger.error(error_detail)
            raise HTTPException(status_code=400, detail={
                "error": "Login failed",
                "message": f"Could not login user '{email}' with any password strategy.",
                "suggestions": [
                    "User may exist with a different password",
                    "Try using /manage-existing-user to create alternative email",
                    "Contact administrator to reset password"
                ]
            })
        
        # Step 3: Save/update session info in DB
        if user_token:
            # Update existing token
            user_token.access_token = session_cookie or "session_based"
            user_token.password = password
            db.commit()
            logger.info(f"✅ Updated existing user token for {email}")
            logger.info(f"Stored session cookie: {user_token.access_token}")
        else:
            # Create new token
            user_token = UserToken(email=email, access_token=session_cookie or "session_based", password=password)
            db.add(user_token)
            db.commit()
            logger.info(f"✅ Created new user token for {email}")
            logger.info(f"Stored session cookie: {user_token.access_token}")
            
    except requests.exceptions.RequestException as e:
        error_detail = f"Open edX request failed: {str(e)}"
        logger.error(error_detail)
        raise HTTPException(status_code=500, detail=error_detail)

    # Step 4: Return response based on format
    # Check if this is an iframe request FIRST (before checking format)
    is_iframe_request = iframe == "1" or embedded == "1" or (request and "iframe" in str(request.headers.get("referer", "")))
    
    if format == "json" and not is_iframe_request:
        # Return JSON response with user info and redirect URL (only if not iframe)
        user_json = {
            "email": email,
            "name": email.split("@")[0],
            "course_id": COURSE_ID,
            "session_cookie": user_token.access_token if user_token else None,
            "authentication_method": "session_based",
            "dashboard_url": OPENEDX_DASHBOARD_URL,
            "redirect_url": f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}?format=redirect",
            "auto_login_url": f"{FASTAPI_PUBLIC_BASE_URL}/auto-login/{email}",
            "message": "User registered and logged in successfully"
        }
        return JSONResponse(content=user_json)
    
    # For iframe requests, ALWAYS return HTML (even if no token yet)
    if is_iframe_request:
        logger.info(f"Returning iframe-friendly HTML for user: {email}")
        
        # Use dashboard-proxy endpoint which handles session properly
        dashboard_url = f"{FASTAPI_PUBLIC_BASE_URL}/dashboard-proxy/{link_id}"
        
        # If we have a valid session token, use it; otherwise dashboard-proxy will handle login
        if user_token and user_token.access_token and user_token.access_token != "session_based":
            dashboard_url = f"{FASTAPI_PUBLIC_BASE_URL}/dashboard-proxy/{link_id}"
        else:
            # No token yet, but still return HTML that will trigger login
            dashboard_url = f"{FASTAPI_PUBLIC_BASE_URL}/dashboard-proxy/{link_id}"
        
        # Create iframe-friendly HTML that loads dashboard-proxy
        dashboard_html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Open edX Dashboard</title>
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                html, body {{
                    width: 100%;
                    height: 100%;
                    overflow: hidden;
                }}
                body {{
                    margin: 0;
                    padding: 0;
                    background: #f5f5f5;
                }}
                .iframe-container {{
                    width: 100%;
                    height: 100vh;
                    border: none;
                    margin: 0;
                    padding: 0;
                }}
                .loading {{
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    background: white;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    color: #666;
                }}
            </style>
        </head>
        <body>
            <div class="loading" id="loading">
                <div>Loading Open edX Dashboard...</div>
            </div>
            <iframe 
                id="dashboard-iframe"
                src="{dashboard_url}" 
                class="iframe-container"
                frameborder="0"
                sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-top-navigation"
                onload="document.getElementById('loading').style.display='none';"
                allow="fullscreen">
            </iframe>
        </body>
        </html>
        """
        
        # Create response with proper headers for iframe embedding
        response = HTMLResponse(content=dashboard_html)
        
        # Set CORS headers to allow embedding from any origin (including localhost)
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        
        # Set cookies if we have a token
        if user_token and user_token.access_token and user_token.access_token != "session_based":
            response.set_cookie(
                key="lms_sessionid",
                value=user_token.access_token,
                path="/",
                httponly=True,
                samesite="lax"
            )
            response.set_cookie(
                key="sessionid", 
                value=user_token.access_token,
                path="/",
                httponly=True,
                samesite="lax"
            )
        
        # Always set link_id cookie for navigation tracking
        response.set_cookie(
            key="edx_link_id",
            value=link_id,
            path="/",
            httponly=False,  # Allow JavaScript to read it if needed
            samesite="lax",
            max_age=86400  # 24 hours
        )
        
        return response
    else:
        # For non-iframe requests, redirect to dashboard proxy
        if user_token and user_token.access_token and user_token.access_token != "session_based":
            logger.info(f"Redirecting to dashboard proxy for user: {email}")
            
            # Create a response that sets the session cookie and redirects
            response = RedirectResponse(url=f"{FASTAPI_PUBLIC_BASE_URL}/dashboard-proxy/{link_id}", status_code=307)
            
            # Set the session cookie for the browser
            response.set_cookie(
                key="lms_sessionid",
                value=user_token.access_token,
                path="/",
                httponly=True,
                samesite="lax"
            )
            response.set_cookie(
                key="sessionid", 
                value=user_token.access_token,
                path="/",
                httponly=True,
                samesite="lax"
            )
            
            # Set link_id cookie for navigation tracking
            response.set_cookie(
                key="edx_link_id",
                value=link_id,
                path="/",
                httponly=False,
                samesite="lax",
                max_age=86400
            )
            
            return response
        else:
            logger.warning(f"No valid session cookie found: {user_token.access_token if user_token else 'No token'}")
            # Fallback: return JSON with instructions
            user_json = {
                "email": email,
                "name": email.split("@")[0],
                "course_id": COURSE_ID,
                "session_cookie": user_token.access_token if user_token else None,
                "authentication_method": "session_based",
                "dashboard_url": OPENEDX_DASHBOARD_URL,
                "redirect_url": f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}?format=redirect",
                "auto_login_url": f"{FASTAPI_PUBLIC_BASE_URL}/auto-login/{email}",
                "message": "No valid session found, please use auto-login endpoint"
            }
            return JSONResponse(content=user_json)


# SSO endpoint: register if needed, login to create session, then redirect
@app.post("/sso")
def sso_login(user: UserData, request: Request, db: Session = Depends(get_db)):
    email = user.email

    # Ensure we have or can create a password for this email
    existing_token = db.query(UserToken).filter(UserToken.email == email).first()
    password = existing_token.password if existing_token and existing_token.password else DEFAULT_USER_PASSWORD

    # 1) Try to register using direct form submission
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "fastapi-edx-bridge/1.0"})
        
        # Get CSRF token from registration page
        reg_page_response = session.get(f"{OPENEDX_API_BASE}/register", timeout=15)
        csrf_token = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
        
        # Generate a valid username from email
        username = generate_username_from_email(email)
        logger.info(f"Generated username for {email}: {username}")
        
        # Prepare registration form data
        reg_data = {
            "email": email,
            "password": password,
            "username": username,
            "name": (user.name or email.split("@")[0]),
            "terms_of_service": "true",
            "honor_code": "true"
        }
        
        headers = {
            "Referer": f"{OPENEDX_API_BASE}/register",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            reg_data["csrfmiddlewaretoken"] = csrf_token
        
        # Submit registration form
        reg_res = session.post(
            f"{OPENEDX_API_BASE}/user_api/v1/account/registration/",
            data=reg_data,
            headers=headers,
            timeout=15
        )
        
        # Handle registration response
        if reg_res.status_code in [200, 201]:
            logger.info(f"User {email} registered successfully")
        elif reg_res.status_code == 409:
            logger.info(f"User {email} already exists, proceeding to login")
        elif reg_res.status_code == 400:
            # Check if it's a validation error we can handle
            try:
                error_data = reg_res.json()
                if "username" in error_data and "already exists" in str(error_data):
                    logger.info(f"User {email} already exists (username conflict), proceeding to login")
                else:
                    raise HTTPException(status_code=502, detail="Open edX registration validation error")
            except:
                raise HTTPException(status_code=502, detail="Open edX registration error")
        else:
            raise HTTPException(status_code=502, detail="Open edX registration error")
            
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Open edX registration unreachable")

    # 2) Create a browser session by hitting login endpoints with CSRF flow
    try:
        # Get CSRF token from login page (or root) - Open edX sets csrftoken cookie
        session.get(f"{OPENEDX_API_BASE}/login", timeout=15)
        csrftoken = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
        headers = {"Referer": f"{OPENEDX_API_BASE}/login"}
        if csrftoken:
            headers["X-CSRFToken"] = csrftoken

        # Use login_ajax or login endpoint. LMS typically exposes /user_api/v1/account/login_session/
        login_data = {
            "email": email, 
            "password": password,
            "username": username
        }
        if csrftoken:
            login_data["csrfmiddlewaretoken"] = csrftoken
            
        login_url = f"{OPENEDX_API_BASE}/user_api/v1/account/login_session/"
        login_res = session.post(login_url, data=login_data, headers=headers, timeout=15)
        if login_res.status_code not in (200, 204):
            # Fallback to classic login form
            form_data = {
                "email": email, 
                "password": password,
                "username": username
            }
            if csrftoken:
                form_data["csrfmiddlewaretoken"] = csrftoken
            login_res = session.post(f"{OPENEDX_API_BASE}/login_ajax", data=form_data, headers=headers, timeout=15)
            
            if login_res.status_code != 200:
                # Try with email only (without username)
                form_data_email_only = {
                    "email": email, 
                    "password": password
                }
                if csrftoken:
                    form_data_email_only["csrfmiddlewaretoken"] = csrftoken
                login_res = session.post(f"{OPENEDX_API_BASE}/login_ajax", data=form_data_email_only, headers=headers, timeout=15)
                
                if login_res.status_code != 200:
                    raise HTTPException(status_code=401, detail="Open edX login failed")

        # Persist token/password locally for re-use
        if not existing_token:
            db.add(UserToken(email=email, access_token="", password=password))
            db.commit()
        elif not existing_token.password:
            existing_token.password = password
            db.commit()

        # Extract session cookies (e.g., sessionid)
        sessionid = session.cookies.get("sessionid") or session.cookies.get("edxsessionid")
        if not sessionid:
            # Some deployments set "edxsession"
            sessionid = session.cookies.get("edxsession")
        if not sessionid:
            raise HTTPException(status_code=502, detail="Open edX session cookie not found")

    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Open edX login unreachable")

    # 3) Redirect to dashboard while setting session cookie for client browser
    response = RedirectResponse(url=OPENEDX_DASHBOARD_URL, status_code=307)
    response.set_cookie(
        key="sessionid",
        value=sessionid,
        domain=request.url.hostname,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    # Also set edx csrftoken if present (best-effort)
    csrftoken = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
    if csrftoken:
        response.set_cookie(
            key="csrftoken",
            value=csrftoken,
            domain=request.url.hostname,
            path="/",
            secure=True,
            httponly=False,
            samesite="lax",
        )

    return response

# Auto-login and redirect endpoint for existing users
@app.get("/auto-login/{email}")
def auto_login_existing_user(email: str, request: Request, db: Session = Depends(get_db)):
    """Automatically login existing user and redirect to dashboard"""
    logger.info(f"Auto-login attempt for existing user: {email}")
    
    # Generate username from email
    username = generate_username_from_email(email)
    
    # Try multiple password strategies for existing users
    password_strategies = [
        DEFAULT_USER_PASSWORD,
        "password123",
        "Password123",
        "123456",
        "admin123",
        "test123",
        "user123",
        "demo123"
    ]
    
    # Try each password strategy
    for password in password_strategies:
        logger.info(f"Trying password strategy for {email}: {password}")
        
        try:
            # Create a session to handle cookies and CSRF tokens
            session = requests.Session()
            session.headers.update({"User-Agent": "fastapi-edx-bridge/1.0"})
            
            # Get CSRF token from login page
            login_page_response = session.get(f"{OPENEDX_API_BASE}/login", timeout=30)
            csrf_token = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
            
            # Prepare login form data
            login_data = {
                "email": email,
                "password": password,
                "username": username
            }
            
            headers = {
                "Referer": f"{OPENEDX_API_BASE}/login",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            if csrf_token:
                headers["X-CSRFToken"] = csrf_token
                login_data["csrfmiddlewaretoken"] = csrf_token
            
            # Submit login form
            login_response = session.post(
                f"{OPENEDX_API_BASE}/user_api/v1/account/login_session/",
                data=login_data,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"Auto-login response status: {login_response.status_code}")
            
            if login_response.status_code not in [200, 204]:
                # Fallback to traditional login form
                login_response = session.post(
                    f"{OPENEDX_API_BASE}/login_ajax",
                    data=login_data,
                    headers=headers,
                    timeout=30
                )
                
                logger.info(f"Auto-login fallback response status: {login_response.status_code}")
                
                if login_response.status_code not in [200, 204]:
                    # If login still fails, try with email only (without username)
                    login_data_email_only = {
                        "email": email,
                        "password": password
                    }
                    if csrf_token:
                        login_data_email_only["csrfmiddlewaretoken"] = csrf_token
                    
                    login_response = session.post(
                        f"{OPENEDX_API_BASE}/login_ajax",
                        data=login_data_email_only,
                        headers=headers,
                        timeout=30
                    )
                    
                    logger.info(f"Auto-login email-only response status: {login_response.status_code}")
            
            # Check if login was successful
            if login_response.status_code in [200, 204]:
                logger.info(f"User {email} auto-logged in successfully with password: {password}")
                
                # Extract session cookies
                sessionid = session.cookies.get("sessionid") or session.cookies.get("edxsessionid")
                if not sessionid:
                    sessionid = session.cookies.get("edxsession")
                
                if sessionid:
                    # Save session info in DB
                    existing_token = db.query(UserToken).filter(UserToken.email == email).first()
                    if existing_token:
                        existing_token.access_token = sessionid
                        existing_token.password = password
                    else:
                        user_token = UserToken(email=email, access_token=sessionid, password=password)
                        db.add(user_token)
                    db.commit()
                    
                    # Redirect to dashboard while setting session cookie for client browser
                    response = RedirectResponse(url=OPENEDX_DASHBOARD_URL, status_code=307)
                    response.set_cookie(
                        key="sessionid",
                        value=sessionid,
                        domain=request.url.hostname,
                        path="/",
                        secure=True,
                        httponly=True,
                        samesite="lax",
                    )
                    
                    # Also set edx csrftoken if present
                    csrftoken = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
                    if csrftoken:
                        response.set_cookie(
                            key="csrftoken",
                            value=csrftoken,
                            domain=request.url.hostname,
                            path="/",
                            secure=True,
                            httponly=False,
                            samesite="lax",
                        )
                    
                    return response
                else:
                    logger.warning(f"Session cookie not found after successful login with password: {password}")
                    continue  # Try next password
            else:
                logger.info(f"Login failed with password: {password}, trying next strategy")
                continue  # Try next password
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request failed with password {password}: {str(e)}, trying next strategy")
            continue  # Try next password
    
    # If all password strategies failed
    logger.error(f"All password strategies failed for user: {email}")
    raise HTTPException(status_code=400, detail={
        "error": "Auto-login failed",
        "message": f"Could not automatically login user '{email}' with any password strategy.",
        "tried_passwords": password_strategies,
        "suggestions": [
            f"Use POST /manage-existing-user to create alternative email",
            "Contact the Open edX administrator to reset the password",
            "Try with a completely different email address"
        ],
        "manage_user_url": f"{FASTAPI_PUBLIC_BASE_URL}/manage-existing-user"
    })

# Custom password login endpoint
@app.post("/custom-login")
def custom_password_login(user_data: dict, request: Request, db: Session = Depends(get_db)):
    """Login with a custom password for existing users"""
    email = user_data.get("email")
    password = user_data.get("password")
    
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    
    logger.info(f"Custom login attempt for user: {email}")
    
    # Generate username from email
    username = generate_username_from_email(email)
    
    try:
        # Create a session to handle cookies and CSRF tokens
        session = requests.Session()
        session.headers.update({"User-Agent": "fastapi-edx-bridge/1.0"})
        
        # Get CSRF token from login page
        login_page_response = session.get(f"{OPENEDX_API_BASE}/login", timeout=30)
        csrf_token = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
        
        # Prepare login form data
        login_data = {
            "email": email,
            "password": password,
            "username": username
        }
        
        headers = {
            "Referer": f"{OPENEDX_API_BASE}/login",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            login_data["csrfmiddlewaretoken"] = csrf_token
        
        # Submit login form
        login_response = session.post(
            f"{OPENEDX_API_BASE}/user_api/v1/account/login_session/",
            data=login_data,
            headers=headers,
            timeout=30
        )
        
        logger.info(f"Custom login response status: {login_response.status_code}")
        
        if login_response.status_code not in [200, 204]:
            # Fallback to traditional login form
            login_response = session.post(
                f"{OPENEDX_API_BASE}/login_ajax",
                data=login_data,
                headers=headers,
                timeout=30
            )
            
            logger.info(f"Custom login fallback response status: {login_response.status_code}")
            
            if login_response.status_code not in [200, 204]:
                # If login still fails, try with email only (without username)
                login_data_email_only = {
                    "email": email,
                    "password": password
                }
                if csrf_token:
                    login_data_email_only["csrfmiddlewaretoken"] = csrf_token
                
                login_response = session.post(
                    f"{OPENEDX_API_BASE}/login_ajax",
                    data=login_data_email_only,
                    headers=headers,
                    timeout=30
                )
                
                logger.info(f"Custom login email-only response status: {login_response.status_code}")
        
        # Check if login was successful
        if login_response.status_code in [200, 204]:
            logger.info(f"User {email} custom login successful")
            
            # Extract session cookies
            sessionid = session.cookies.get("sessionid") or session.cookies.get("edxsessionid")
            if not sessionid:
                sessionid = session.cookies.get("edxsession")
            
            if sessionid:
                # Save session info in DB
                existing_token = db.query(UserToken).filter(UserToken.email == email).first()
                if existing_token:
                    existing_token.access_token = sessionid
                    existing_token.password = password
                else:
                    user_token = UserToken(email=email, access_token=sessionid, password=password)
                    db.add(user_token)
                db.commit()
                
                # Redirect to dashboard while setting session cookie for client browser
                response = RedirectResponse(url=OPENEDX_DASHBOARD_URL, status_code=307)
                response.set_cookie(
                    key="sessionid",
                    value=sessionid,
                    domain=request.url.hostname,
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="lax",
                )
                
                # Also set edx csrftoken if present
                csrftoken = session.cookies.get("csrftoken") or session.cookies.get("edxcsrftoken")
                if csrftoken:
                    response.set_cookie(
                        key="csrftoken",
                        value=csrftoken,
                        domain=request.url.hostname,
                        path="/",
                        secure=True,
                        httponly=False,
                        samesite="lax",
                    )
                
                return response
            else:
                raise HTTPException(status_code=500, detail="Session cookie not found after successful login")
        else:
            # Login failed
            error_detail = f"Custom login failed for user '{email}'. Status: {login_response.status_code}"
            logger.error(error_detail)
            raise HTTPException(status_code=400, detail={
                "error": "Custom login failed",
                "message": f"Could not login user '{email}' with the provided password.",
                "suggestions": [
                    "Verify the password is correct",
                    "Use the /manage-existing-user endpoint to create alternative email",
                    "Contact the Open edX administrator to reset the password"
                ]
            })
            
    except requests.exceptions.RequestException as e:
        error_detail = f"Custom login request failed: {str(e)}"
        logger.error(error_detail)
        raise HTTPException(status_code=500, detail=error_detail)

# Test endpoint to check user status
@app.get("/user-status/{email}")
def check_user_status(email: str, db: Session = Depends(get_db)):
    """Check if a user exists in our database and their status"""
    user_token = db.query(UserToken).filter(UserToken.email == email).first()
    user_link = db.query(UserLink).filter(UserLink.email == email).first()
    
    return {
        "email": email,
        "has_token": user_token is not None,
        "has_link": user_link is not None,
        "username": generate_username_from_email(email) if user_token else None,
        "stored_password": user_token.password if user_token and user_token.password else None
    }

# User management endpoint for existing users
@app.post("/manage-existing-user")
def manage_existing_user(user: UserData, db: Session = Depends(get_db)):
    """Handle existing users by creating a new user with a modified email"""
    original_email = user.email
    base_email = original_email.split("@")[0]
    domain = original_email.split("@")[1]
    
    # Try different email variations
    email_variations = [
        f"{base_email}+fastapi@{domain}",
        f"{base_email}_fastapi@{domain}",
        f"{base_email}_new@{domain}",
        f"{base_email}2@{domain}",
        f"{base_email}_auto@{domain}"
    ]
    
    for new_email in email_variations:
        # Check if this email is already in our database
        existing_user = db.query(UserToken).filter(UserToken.email == new_email).first()
        if not existing_user:
            # Create a new link for this email
            link_id = str(uuid.uuid4())
            new_link = UserLink(link_id=link_id, email=new_email)
            db.add(new_link)
            db.commit()
            
            return {
                "message": f"Created new user with email: {new_email}",
                "original_email": original_email,
                "new_email": new_email,
                "link": f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}",
                "reason": "Original email already exists in Open edX with different password"
            }
    
    return {
        "error": "Could not create alternative email",
        "message": f"All email variations for {original_email} are already in use",
        "suggestions": [
            "Try with a completely different email address",
            "Contact administrator to reset password for existing user",
            "Use a different domain for the email"
        ]
    }

# Test endpoint to demonstrate the complete flow
@app.get("/test-flow/{email}")
def test_complete_flow(email: str, db: Session = Depends(get_db)):
    """Test the complete flow for a user"""
    logger.info(f"Testing complete flow for user: {email}")
    
    # Check if user exists in our database
    user_token = db.query(UserToken).filter(UserToken.email == email).first()
    user_link = db.query(UserLink).filter(UserLink.email == email).first()
    
    username = generate_username_from_email(email)
    
    return {
        "email": email,
        "username": username,
        "user_exists_in_db": user_token is not None,
        "has_link": user_link is not None,
        "flow_options": {
            "1_register_new": f"POST /generate-link with {email}",
            "2_auto_login": f"GET /auto-login/{email}",
            "3_manage_existing": f"POST /manage-existing-user with {email}",
            "4_sso_redirect": f"POST /sso with {email}"
        },
        "recommended_flow": "auto_login" if user_token else "register_new",
        "dashboard_url": OPENEDX_DASHBOARD_URL
    }

# ICG API Configuration
ICG_API_BASE = os.getenv("ICG_API_BASE", "http://localhost:3000")
ICG_WEBHOOK_ENDPOINT = os.getenv("ICG_WEBHOOK_ENDPOINT", "/openedx/course-completed")

# Webhook endpoint to receive course completion from edX and forward to ICG API
@app.post("/webhook/course-completed")
async def course_completed_webhook(payload: dict, request: Request):
    """
    Receive course completion webhook from edX and forward to ICG API.
    This endpoint is called by edX when a certificate is generated.
    """
    logger.info(f"Received course completion webhook: {payload}")
    
    try:
        # Validate payload
        if not payload.get('username') or not payload.get('courseId'):
            logger.warning(f"Invalid webhook payload: missing username or courseId")
            raise HTTPException(status_code=400, detail="Missing required fields: username, courseId")
        
        # Forward to ICG API
        icg_url = f"{ICG_API_BASE}{ICG_WEBHOOK_ENDPOINT}"
        logger.info(f"Forwarding webhook to ICG API: {icg_url}")
        
        response = requests.post(
            icg_url,
            json=payload,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'FastAPI-edX-Webhook/1.0'
            },
            timeout=30
        )
        
        if response.status_code in [200, 201, 204]:
            logger.info(f"Successfully forwarded webhook to ICG API for user {payload.get('username')}, course {payload.get('courseId')}")
            return {
                "status": "success",
                "message": "Webhook forwarded to ICG API",
                "icg_response": response.json() if response.content else None
            }
        else:
            logger.error(
                f"Failed to forward webhook to ICG API. Status: {response.status_code}, "
                f"Response: {response.text}"
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=f"ICG API returned error: {response.text}"
            )
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error forwarding webhook to ICG API: {str(e)}")
        raise HTTPException(status_code=502, detail=f"Failed to connect to ICG API: {str(e)}")
    except Exception as e:
        logger.error(f"Error processing course completion webhook: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
