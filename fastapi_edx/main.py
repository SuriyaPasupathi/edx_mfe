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
from urllib.parse import urljoin, quote, unquote, parse_qs
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
LEARNING_MFE_URL = os.getenv("LEARNING_MFE_URL", "http://localhost:2000").rstrip('/')
LEARNER_DASHBOARD_MFE_URL = os.getenv("LEARNER_DASHBOARD_MFE_URL", "http://localhost:1996").rstrip('/')
AUTHN_MFE_URL = os.getenv("AUTHN_MFE_URL", "http://localhost:1999").rstrip('/')
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

def forward_cookies_from_response(response: requests.Response, fastapi_response: Response, link_id: str = None):
    """
    Forward cookies from Open edX response to FastAPI response.
    This ensures session cookies and CSRF tokens are available to the browser.
    """
    # Forward CSRF cookies
    csrf_cookie = response.cookies.get("csrftoken") or response.cookies.get("edxcsrftoken")
    if csrf_cookie:
        fastapi_response.set_cookie(
            key="csrftoken",
            value=csrf_cookie,
            path="/",
            httponly=False,  # CSRF tokens need to be accessible to JavaScript
            samesite="none",
            secure=False,
            max_age=86400 * 7  # 7 days
        )
        logger.info(f"Forwarded CSRF cookie: {csrf_cookie[:20]}...")
    
    # Forward session cookies (critical for maintaining session)
    session_cookie_names = ["sessionid", "lms_sessionid", "edxsessionid", "session", "edx_session"]
    for cookie_name in session_cookie_names:
        cookie_value = response.cookies.get(cookie_name)
        if cookie_value:
            fastapi_response.set_cookie(
                key=cookie_name,
                value=cookie_value,
                path="/",
                httponly=True,  # Session cookies should be httpOnly for security
                samesite="none",
                secure=False,
                max_age=86400 * 7  # 7 days
            )
            logger.info(f"Forwarded session cookie {cookie_name}: {cookie_value[:20]}...")
    
    # Set link_id cookie if provided
    if link_id:
        fastapi_response.set_cookie(
            key="edx_link_id",
            value=link_id,
            path="/",
            httponly=False,
            samesite="none",
            secure=False,
            max_age=86400
        )

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
    if not user_token or not user_token.access_token or user_token.access_token == "session_based":
        logger.info(f"No valid session found for user {email}, redirecting to access endpoint")
        return RedirectResponse(url=f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}?format=redirect", status_code=307)

    # Create a session with the stored cookies
    session = requests.Session()
    session.headers.update({"User-Agent": "fastapi-edx-bridge/1.0"})
    
    # Set session cookies - try both names (don't set domain, let requests handle it)
    if user_token.access_token:
        session.cookies.set("lms_sessionid", user_token.access_token)
        session.cookies.set("sessionid", user_token.access_token)
        logger.info(f"Set session cookies for dashboard request: {user_token.access_token[:30]}...")
    
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
        
        # Fetch the dashboard content with the session, following redirects
        dashboard_response = session.get(dashboard_url, timeout=30, allow_redirects=True)
        
        # Log the final URL after redirects
        logger.info(f"Dashboard response status: {dashboard_response.status_code}, final URL: {dashboard_response.url}")
        logger.info(f"Response cookies: {dict(dashboard_response.cookies)}")
        logger.info(f"Response content length: {len(dashboard_response.text) if dashboard_response.text else 0}")
        
        # Update stored session cookie if Open edX returned a new one
        if dashboard_response.cookies.get("lms_sessionid"):
            new_session = dashboard_response.cookies.get("lms_sessionid")
            if new_session != user_token.access_token:
                user_token.access_token = new_session
                db.commit()
                logger.info(f"Updated session cookie from dashboard response")
        
        # Handle both 200 OK and redirects that result in 200 OK
        if dashboard_response.status_code == 200:
            # Check if we got actual HTML content or if it's a redirect page
            content_length = len(dashboard_response.text.strip()) if dashboard_response.text else 0
            final_url_after_redirect = dashboard_response.url
            
            # If content is too short (< 1000 chars) and final URL is different (redirected to MFE), fetch the MFE content
            if content_length < 1000 and final_url_after_redirect and final_url_after_redirect != dashboard_url:
                # Check if it redirected to an MFE
                if '/learner-dashboard' in final_url_after_redirect or ':1996' in final_url_after_redirect:
                    logger.info(f"Dashboard redirected to MFE, fetching MFE content from: {final_url_after_redirect}")
                    try:
                        # Fetch the MFE content with session cookies
                        mfe_response = session.get(final_url_after_redirect, timeout=30, allow_redirects=True)
                        if mfe_response.status_code == 200 and len(mfe_response.text) > 1000:
                            logger.info(f"Got MFE content, length: {len(mfe_response.text)}")
                            # Use the MFE content instead - it will be processed below
                            dashboard_response = mfe_response  # Replace the response so it gets processed
                            # Update response cookies if MFE returned new ones
                            if mfe_response.cookies.get("lms_sessionid"):
                                new_session = mfe_response.cookies.get("lms_sessionid")
                                if new_session != user_token.access_token:
                                    user_token.access_token = new_session
                                    db.commit()
                                    logger.info(f"Updated session cookie from MFE response")
                        else:
                            logger.warning(f"MFE response too short or failed: {mfe_response.status_code}, length: {len(mfe_response.text) if mfe_response.text else 0}")
                            # Fall back to embedding MFE in iframe with session cookies
                            logger.info(f"Embedding MFE in iframe: {final_url_after_redirect}")
                            mfe_html = f"""
                            <!DOCTYPE html>
                            <html>
                            <head>
                                <meta charset="UTF-8">
                                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                                <title>Open edX Dashboard</title>
                                <style>
                                    body {{ margin: 0; padding: 0; overflow: hidden; }}
                                    iframe {{ width: 100%; height: 100vh; border: none; }}
                                </style>
                            </head>
                            <body>
                                <iframe 
                                    id="mfe-iframe"
                                    src="{final_url_after_redirect}" 
                                    frameborder="0" 
                                    allow="fullscreen" 
                                    sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-top-navigation">
                                </iframe>
                                <script>
                                    // Set session cookies for the iframe domain
                                    document.cookie = "lms_sessionid={user_token.access_token}; path=/; SameSite=None; Secure=false";
                                    document.cookie = "sessionid={user_token.access_token}; path=/; SameSite=None; Secure=false";
                                </script>
                            </body>
                            </html>
                            """
                            response = HTMLResponse(content=mfe_html)
                            response.headers["X-Frame-Options"] = "ALLOWALL"
                            response.headers["Content-Security-Policy"] = "frame-ancestors *"
                            response.headers["Access-Control-Allow-Origin"] = "*"
                            # Set session cookies for the browser
                            response.set_cookie(key="lms_sessionid", value=user_token.access_token, path="/", httponly=True, samesite="lax", secure=False)
                            response.set_cookie(key="sessionid", value=user_token.access_token, path="/", httponly=True, samesite="lax", secure=False)
                            return response
                    except Exception as e:
                        logger.error(f"Failed to fetch MFE content: {e}")
            
            # Check if we got actual HTML content
            if not dashboard_response.text or content_length < 100:
                logger.warning(f"Dashboard response is empty or too short: {content_length} chars")
                # Try refreshing the session
                logger.info("Attempting to refresh session by redirecting to access endpoint")
                return RedirectResponse(url=f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}?format=redirect", status_code=307)
            # Process the HTML content to fix relative URLs and navigation
            dashboard_content = dashboard_response.text
            
            # Replace relative URLs with our proxy URLs to maintain session
            # Handle static assets (CSS, JS, images) with static proxy
            dashboard_content = dashboard_content.replace('src="/static/', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            dashboard_content = dashboard_content.replace('href="/static/', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            dashboard_content = dashboard_content.replace('url("/static/', f'url("{FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            dashboard_content = dashboard_content.replace('url(/static/', f'url({FASTAPI_PUBLIC_BASE_URL}/openedx-static/static/')
            
            # Handle asset URLs with static proxy
            dashboard_content = re.sub(r'src="(/asset-v1:[^"]*)"', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1', dashboard_content)
            dashboard_content = re.sub(r'href="(/asset-v1:[^"]*)"', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1', dashboard_content)
            
            # Handle MFE assets (learner-dashboard, authn, etc.) - these need to be proxied BEFORE general href/src replacement
            # Replace /learner-dashboard/ paths with proxied paths - use non-greedy matching to stop at first quote
            dashboard_content = re.sub(r'src="(/learner-dashboard/[^"]+?)"', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1"', dashboard_content)
            dashboard_content = re.sub(r'href="(/learner-dashboard/[^"]+?)"', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1"', dashboard_content)
            dashboard_content = re.sub(r'url\("(/learner-dashboard/[^"]+?)"\)', f'url("{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1")', dashboard_content)
            
            # Handle authn MFE assets - use non-greedy matching
            dashboard_content = re.sub(r'src="(/authn/[^"]+?)"', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1"', dashboard_content)
            dashboard_content = re.sub(r'href="(/authn/[^"]+?)"', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1"', dashboard_content)
            dashboard_content = re.sub(r'url\("(/authn/[^"]+?)"\)', f'url("{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1")', dashboard_content)
            
            # Handle other common MFE paths
            mfe_paths = ['/learning/', '/course-authoring/', '/account/', '/profile/']
            for mfe_path in mfe_paths:
                dashboard_content = re.sub(rf'src="({re.escape(mfe_path)}[^"]*)"', f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1', dashboard_content)
                dashboard_content = re.sub(rf'href="({re.escape(mfe_path)}[^"]*)"', f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static\\1', dashboard_content)
            
            # Handle other relative URLs with navigation proxy (include link_id in query)
            def add_link_id_to_href_dash(match):
                url_path = match.group(1)
                if '?' in url_path or url_path.startswith('http') or FASTAPI_PUBLIC_BASE_URL in match.group(0):
                    return match.group(0)
                # For asset URLs and static files, use static proxy (no link_id needed)
                if url_path.startswith('asset-v1:') or url_path.startswith('static/'):
                    return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static{url_path}"'
                # Skip MFE paths that should already be handled above
                if url_path.startswith('/learner-dashboard/') or url_path.startswith('/authn/'):
                    return match.group(0)  # Already handled above
                separator = '&' if '?' in url_path else '?'
                return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            def add_link_id_to_action_dash(match):
                url_path = match.group(1)
                if '?' in url_path or url_path.startswith('http'):
                    return match.group(0)
                separator = '&' if '?' in url_path else '?'
                return f'action="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            # Replace href="/path" with href="/openedx-proxy/path?link_id=..."
            dashboard_content = re.sub(r'href="(/[^"]*)"', add_link_id_to_href_dash, dashboard_content)
            # Replace action="/path" with action="/openedx-proxy/path?link_id=..."
            dashboard_content = re.sub(r'action="(/[^"]*)"', add_link_id_to_action_dash, dashboard_content)
            # For src, use static proxy (handle asset URLs too)
            # But skip if already proxied (contains FASTAPI_PUBLIC_BASE_URL)
            def replace_src_dash(match):
                url_path = match.group(1)
                if url_path.startswith('http') or FASTAPI_PUBLIC_BASE_URL in match.group(0):
                    return match.group(0)
                # Skip MFE paths that should already be handled above
                if url_path.startswith('/learner-dashboard/') or url_path.startswith('/authn/'):
                    return match.group(0)  # Already handled above
                return f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static{url_path}"'
            dashboard_content = re.sub(r'src="(/[^"]*)"', replace_src_dash, dashboard_content)
            
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
                    .dashboard-content {{
                        background: white;
                        margin: 0;
                        padding: 0;
                        border-radius: 0;
                        box-shadow: none;
                        overflow: hidden;
                        min-height: 100vh;
                    }}
                    .loading {{
                        text-align: center;
                        padding: 50px;
                        color: #666;
                    }}
                </style>
            </head>
            <body>
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
            # Use SameSite=None and Secure=False for cross-origin iframe embedding (HTTP)
            # Note: For HTTPS, use Secure=True
            response.set_cookie(
                key="edx_link_id",
                value=link_id,
                path="/",
                httponly=False,  # Allow JavaScript to read it if needed
                samesite="none",  # Changed to "none" for cross-origin
                secure=False,  # Set to False for HTTP, True for HTTPS
                max_age=86400  # 24 hours
            )
            
            # Also forward session cookies from the response to the browser
            if dashboard_response.cookies.get("lms_sessionid"):
                response.set_cookie(
                    key="lms_sessionid",
                    value=dashboard_response.cookies.get("lms_sessionid"),
                    path="/",
                    httponly=True,
                    samesite="lax",
                    secure=False
                )
            if dashboard_response.cookies.get("csrftoken"):
                response.set_cookie(
                    key="csrftoken",
                    value=dashboard_response.cookies.get("csrftoken"),
                    path="/",
                    httponly=False,
                    samesite="lax",
                    secure=False
                )
            
            return response
        elif dashboard_response.status_code in [301, 302, 303, 307, 308]:
            # Handle redirects - Open edX might redirect dashboard to MFE or another URL
            redirect_location = dashboard_response.headers.get("Location", "")
            final_url = dashboard_response.url if hasattr(dashboard_response, 'url') else dashboard_url
            
            logger.warning(f"Dashboard returned redirect {dashboard_response.status_code}")
            logger.warning(f"Redirect location header: {redirect_location}")
            logger.warning(f"Final URL after redirects: {final_url}")
            
            # If redirect goes to an MFE (like learner dashboard), embed it in iframe
            mfe_patterns = ['/learner-dashboard', '/dashboard', ':1996', ':1997', ':2000']
            is_mfe_redirect = any(pattern in (redirect_location + final_url) for pattern in mfe_patterns)
            
            if is_mfe_redirect or redirect_location.startswith('http://localhost:') or redirect_location.startswith('http://127.0.0.1:'):
                # This is likely an MFE redirect - try to fetch the final content
                if final_url and final_url != dashboard_url:
                    logger.info(f"Fetching redirected MFE content from: {final_url}")
                    try:
                        final_response = session.get(final_url, timeout=30, allow_redirects=True)
                        if final_response.status_code == 200:
                            # Return the MFE content in an iframe wrapper
                            mfe_content = final_response.text
                            mfe_html = f"""
                            <!DOCTYPE html>
                            <html>
                            <head>
                                <meta charset="UTF-8">
                                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                                <title>Open edX Dashboard</title>
                                <style>
                                    body {{ margin: 0; padding: 0; overflow: hidden; }}
                                    iframe {{ width: 100%; height: 100vh; border: none; }}
                                </style>
                            </head>
                            <body>
                                <iframe src="{final_url}" frameborder="0" allow="fullscreen"></iframe>
                            </body>
                            </html>
                            """
                            response = HTMLResponse(content=mfe_html)
                            response.headers["X-Frame-Options"] = "ALLOWALL"
                            response.headers["Content-Security-Policy"] = "frame-ancestors *"
                            return response
                    except Exception as e:
                        logger.error(f"Failed to fetch MFE content: {e}")
            
            # If redirect location is relative, make it absolute
            if redirect_location and redirect_location.startswith("/"):
                redirect_location = f"{OPENEDX_API_BASE}{redirect_location}"
            
            # For other redirects, try to follow them by redirecting to access endpoint to refresh session
            logger.info(f"Redirecting to access endpoint to refresh session and follow redirect")
            return RedirectResponse(url=f"{FASTAPI_PUBLIC_BASE_URL}/access/{link_id}?format=redirect", status_code=307)
        else:
            # If dashboard fetch fails, return a helpful error page
            logger.error(f"Dashboard fetch failed with status {dashboard_response.status_code}")
            logger.error(f"Response text: {dashboard_response.text[:500]}")
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

# OPTIONS handler for static proxy CORS preflight requests
@app.options("/openedx-static/{path:path}")
async def openedx_static_proxy_options(path: str, request: Request):
    """Handle CORS preflight requests for static assets"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Credentials": "true"
        }
    )

# Static assets proxy endpoint (no authentication required)
@app.get("/openedx-static/{path:path}")
def openedx_static_proxy(path: str, request: Request):
    """Proxy endpoint for static assets (CSS, JS, images, assets) - no authentication required"""
    # Clean the path - remove any HTML tags or extra characters that might have been captured
    # Extract just the filename/path before any HTML tags
    clean_path = path.split('>')[0].split('<')[0].split('"')[0].split("'")[0]
    clean_path = clean_path.strip()
    
    # Route MFE assets to their respective MFE servers
    # Learner Dashboard MFE assets
    if clean_path.startswith("learner-dashboard/"):
        mfe_path = clean_path.replace("learner-dashboard/", "", 1)  # Remove the prefix
        # Extract just the filename if there are query params or fragments
        mfe_path = mfe_path.split('?')[0].split('#')[0]
        openedx_url = f"{LEARNER_DASHBOARD_MFE_URL}/{mfe_path}"
        logger.info(f"Routing learner-dashboard asset to MFE: {openedx_url}")
    # Authn MFE assets
    elif clean_path.startswith("authn/"):
        mfe_path = clean_path.replace("authn/", "", 1)  # Remove the prefix
        mfe_path = mfe_path.split('?')[0].split('#')[0]
        openedx_url = f"{AUTHN_MFE_URL}/{mfe_path}"
        logger.info(f"Routing authn asset to MFE: {openedx_url}")
    # Learning MFE assets
    elif clean_path.startswith("learning/"):
        mfe_path = clean_path.replace("learning/", "", 1)  # Remove the prefix
        mfe_path = mfe_path.split('?')[0].split('#')[0]
        openedx_url = f"{LEARNING_MFE_URL}/{mfe_path}"
        logger.info(f"Routing learning asset to MFE: {openedx_url}")
    # Default: route to Open edX LMS
    else:
        # Construct the full Open edX URL for static assets
        # FastAPI already URL-decodes the path, so we use it as-is
        # Open edX expects asset URLs with special characters (colons, plus signs) as-is
        openedx_url = f"{OPENEDX_API_BASE}/{clean_path}"
    
    # Add query parameters if present
    if request.query_params:
        query_string = "&".join([f"{k}={quote(str(v), safe='')}" for k, v in request.query_params.items()])
        openedx_url += "?" + query_string
    
    logger.info(f"Proxying static asset request: {openedx_url}")
    
    try:
        # Prepare headers for forwarding to Open edX
        headers = {
            "User-Agent": request.headers.get("user-agent", "fastapi-edx-proxy/1.0"),
            "Accept": request.headers.get("accept", "*/*"),
            "Accept-Language": request.headers.get("accept-language", ""),
        }
        
        # Fetch the static asset directly from Open edX
        response = requests.get(
            openedx_url, 
            timeout=30, 
            allow_redirects=True,
            headers=headers,
            stream=False
        )
        
        logger.info(f"Open edX response status: {response.status_code} for {openedx_url}")
        
        # Get content type from response
        content_type = response.headers.get('content-type', 'application/octet-stream')
        
        # Get response content safely
        response_content = b""
        if response.content:
            response_content = response.content
        elif hasattr(response, 'text') and response.text:
            response_content = response.text.encode('utf-8')
        
        # Prepare response headers
        response_headers = {
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "X-Frame-Options": "ALLOWALL",
            "Content-Security-Policy": "frame-ancestors *"
        }
        
        # Copy relevant headers from Open edX response (only if they exist)
        header_mappings = {
            'content-length': 'Content-Length',
            'content-encoding': 'Content-Encoding',
            'etag': 'ETag',
            'last-modified': 'Last-Modified',
            'content-disposition': 'Content-Disposition'
        }
        
        for source_header, target_header in header_mappings.items():
            if source_header in response.headers:
                response_headers[target_header] = response.headers[source_header]
        
        # Return response with proper status code (pass through Open edX status code)
        return Response(
            content=response_content,
            status_code=response.status_code,
            media_type=content_type,
            headers=response_headers
        )
            
    except requests.exceptions.Timeout as e:
        logger.error(f"Static asset request timeout: {str(e)} for URL: {openedx_url}")
        return Response(
            content=b"Request timeout",
            status_code=504,
            media_type="text/plain",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Static asset connection error: {str(e)} for URL: {openedx_url}")
        return Response(
            content=b"Connection error",
            status_code=502,
            media_type="text/plain",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Static asset request failed: {str(e)} for URL: {openedx_url}")
        return Response(
            content=f"Request failed: {str(e)}".encode(),
            status_code=500,
            media_type="text/plain",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
    except Exception as e:
        logger.error(f"Unexpected error in static proxy: {str(e)} for URL: {openedx_url}", exc_info=True)
        return Response(
            content=f"Internal error: {str(e)}".encode(),
            status_code=500,
            media_type="text/plain",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )

# Navigation proxy endpoint to handle all Open edX requests within the iframe
@app.get("/openedx-proxy/{path:path}")
def openedx_proxy(path: str, request: Request, db: Session = Depends(get_db)):
    """Proxy endpoint to handle navigation within Open edX"""
    # For static assets and asset URLs, redirect to static proxy (no authentication needed)
    if path.startswith('static/') or path.startswith('asset-v1:'):
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
        response = session.get(openedx_url, timeout=30, allow_redirects=False)
        
        # Handle redirects
        if response.status_code in [301, 302, 303, 307, 308]:
            location = response.headers.get("Location", "")
            if location:
                # Intercept Learning MFE URLs (localhost:2000) and convert to proxy
                if LEARNING_MFE_URL in location or "localhost:2000" in location or ":2000" in location:
                    from urllib.parse import urlparse
                    parsed = urlparse(location)
                    mfe_path = parsed.path
                    
                    # Check if we're already in a redirect loop (requesting courseware that redirects to MFE)
                    # If the current path is courseware and it's redirecting to MFE, we need to handle it differently
                    current_path = path.lower()
                    # Specifically check if this is a courseware request (not just any /courses/ path)
                    is_courseware_request = "/courseware" in current_path
                    
                    if is_courseware_request:
                        # We're in a loop - Open edX wants to redirect to Learning MFE for courseware
                        # Instead of redirecting back, return an HTML page that embeds the Learning MFE
                        # But proxy it through our service to maintain session
                        if "/course/" in mfe_path:
                            course_match = re.search(r'/course/([^/]+)', mfe_path)
                            if course_match:
                                course_id = course_match.group(1)
                                # Extract course ID from current path if not found in MFE path
                                if not course_id:
                                    course_match_current = re.search(r'/courses/([^/]+)', current_path)
                                    if course_match_current:
                                        course_id = course_match_current.group(1)
                                
                                if course_id:
                                    # Return HTML that embeds Learning MFE in iframe, but proxied
                                    # Use the Learning MFE URL directly but in an iframe that maintains session
                                    mfe_url = f"{LEARNING_MFE_URL}/course/{course_id}"
                                    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Course Content</title>
    <style>
        body, html {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }}
        iframe {{ width: 100%; height: 100vh; border: none; }}
    </style>
</head>
<body>
    <iframe src="{mfe_url}" allow="fullscreen" allowfullscreen></iframe>
</body>
</html>
"""
                                    html_response = HTMLResponse(content=html_content, status_code=200)
                                    forward_cookies_from_response(response, html_response, link_id)
                                    logger.info(f"Detected redirect loop for courseware, returning iframe with Learning MFE: {mfe_url}")
                                    return html_response
                        
                        # Fallback: redirect to dashboard
                        location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard"
                    else:
                        # Normal Learning MFE redirect - convert to proxy URL
                        if "/course/" in mfe_path:
                            course_match = re.search(r'/course/([^/]+)', mfe_path)
                            if course_match:
                                course_id = course_match.group(1)
                                # Don't redirect back to courseware if that's what caused the redirect
                                # Instead, redirect to course about page or dashboard
                                location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/courses/{course_id}/about"
                            else:
                                location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard"
                        else:
                            location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard"
                    
                    if "?" in location:
                        location += f"&link_id={link_id}"
                    else:
                        location += f"?link_id={link_id}"
                    logger.info(f"Intercepted Learning MFE redirect in GET, converted to proxy URL: {location}")
                
                # Convert Open edX URL to proxy URL if needed
                elif location.startswith(OPENEDX_API_BASE):
                    location = location.replace(OPENEDX_API_BASE, f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy")
                    if "?" in location:
                        location += f"&link_id={link_id}"
                    else:
                        location += f"?link_id={link_id}"
                elif location.startswith("/") and not location.startswith("/openedx-proxy/"):
                    location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{location}"
                    if "?" in location:
                        location += f"&link_id={link_id}"
                    else:
                        location += f"?link_id={link_id}"
                
                redirect_response = RedirectResponse(url=location, status_code=response.status_code)
                forward_cookies_from_response(response, redirect_response, link_id)
                return redirect_response
        
        if response.status_code == 200:
            # Process the content
            content = response.text
            
            # Replace Learning MFE URLs (localhost:2000) with proxy URLs to prevent iframe issues
            # This ensures all navigation stays within the proxy context
            content = re.sub(
                rf'{re.escape(LEARNING_MFE_URL)}/course/([^"\s\'<>]+)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/courses/\1/courseware?link_id={link_id}',
                content
            )
            content = re.sub(
                rf'{re.escape(LEARNING_MFE_URL)}([^"\s\'<>]*)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard?link_id={link_id}',
                content
            )
            # Also catch any localhost:2000 references
            content = re.sub(
                r'http://localhost:2000([^"\s\'<>]*)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard?link_id={link_id}',
                content
            )
            content = re.sub(
                r'https://localhost:2000([^"\s\'<>]*)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard?link_id={link_id}',
                content
            )
            
            # Replace relative URLs with our proxy URLs
            # Include link_id in query parameter as fallback (since cookies may not work cross-origin)
            def add_link_id_to_href(match):
                url_path = match.group(1)
                # Skip if already has query params or is external URL
                if '?' in url_path or url_path.startswith('http'):
                    return match.group(0)
                # Add link_id as query parameter
                separator = '&' if '?' in url_path else '?'
                return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            def add_link_id_to_action(match):
                url_path = match.group(1)
                if '?' in url_path or url_path.startswith('http'):
                    return match.group(0)
                separator = '&' if '?' in url_path else '?'
                return f'action="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            # Replace href="/path" with href="/openedx-proxy/path?link_id=..."
            # But redirect asset URLs and static files to static proxy
            def add_link_id_to_href_smart(match):
                url_path = match.group(1)
                # Skip if already has query params or is external URL
                if '?' in url_path or url_path.startswith('http'):
                    return match.group(0)
                # For asset URLs and static files, use static proxy (no link_id needed)
                if url_path.startswith('asset-v1:') or url_path.startswith('static/'):
                    return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static{url_path}"'
                # Add link_id as query parameter for other URLs
                separator = '&' if '?' in url_path else '?'
                return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            # Replace href="/path" with appropriate proxy URL
            content = re.sub(r'href="(/[^"]*)"', add_link_id_to_href_smart, content)
            # Replace action="/path" with action="/openedx-proxy/path?link_id=..."
            content = re.sub(r'action="(/[^"]*)"', add_link_id_to_action, content)
            # For src, use static proxy (no link_id needed) - handle asset URLs too
            def replace_src(match):
                url_path = match.group(1)
                if url_path.startswith('http'):
                    return match.group(0)
                return f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static{url_path}"'
            content = re.sub(r'src="(/[^"]*)"', replace_src, content)
            
            # Add base tag
            if '<head>' in content:
                content = content.replace('<head>', f'<head><base href="{OPENEDX_API_BASE}/">')
            
            # Create response with cookies forwarded from Open edX
            # Use SameSite=None for cross-origin iframe embedding
            html_response = HTMLResponse(content=content)
            
            # Forward all cookies (CSRF and session) from Open edX response
            forward_cookies_from_response(response, html_response, link_id)
            
            return html_response
        else:
            raise HTTPException(status_code=response.status_code, detail="Open edX request failed")
            
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Proxy request failed: {str(e)}")

# OPTIONS handler for CORS preflight requests
@app.options("/openedx-proxy/{path:path}")
async def openedx_proxy_options(path: str, request: Request):
    """Handle CORS preflight requests"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Credentials": "true"
        }
    )

# POST handler for form submissions (enrollment, etc.)
@app.post("/openedx-proxy/{path:path}")
async def openedx_proxy_post(path: str, request: Request, db: Session = Depends(get_db)):
    """Proxy endpoint to handle POST requests (form submissions) within Open edX"""
    # Extract link_id from multiple sources (same logic as GET handler)
    referer = request.headers.get("referer", "")
    link_id = None
    
    # Strategy 1: Try to extract link_id from referer URL
    if referer:
        if "/dashboard-proxy/" in referer:
            link_id = referer.split("/dashboard-proxy/")[1].split("?")[0].split("#")[0].split("/")[0]
        elif "/access/" in referer:
            link_id = referer.split("/access/")[1].split("?")[0].split("#")[0].split("/")[0]
        elif "/openedx-proxy/" in referer:
            if "link_id=" in referer:
                link_id = referer.split("link_id=")[1].split("&")[0].split("#")[0]
    
    # Strategy 2: Try to get link_id from cookies
    if not link_id:
        link_id = request.cookies.get("edx_link_id")
    
    # Strategy 3: Try to get link_id from query parameters
    if not link_id:
        link_id = request.query_params.get("link_id")
    
    # Strategy 4: Try to find link_id from session cookie
    if not link_id:
        session_cookie = request.cookies.get("lms_sessionid") or request.cookies.get("sessionid")
        if session_cookie:
            user_token = db.query(UserToken).filter(UserToken.access_token == session_cookie).first()
            if user_token:
                user_link = db.query(UserLink).filter(UserLink.email == user_token.email).first()
                if user_link:
                    link_id = user_link.link_id
    
    if not link_id:
        logger.warning(f"Could not extract link_id from POST request. Referer: {referer}, cookies: {dict(request.cookies)}")
        raise HTTPException(status_code=400, detail="Invalid request - link_id not found")
    
    # Get user session
    user_link = db.query(UserLink).filter(UserLink.link_id == link_id).first()
    if not user_link:
        raise HTTPException(status_code=404, detail="Invalid link")
    
    user_token = db.query(UserToken).filter(UserToken.email == user_link.email).first()
    if not user_token or not user_token.access_token:
        raise HTTPException(status_code=400, detail="No valid session found")
    
    # Construct the full Open edX URL
    openedx_url = f"{OPENEDX_API_BASE}/{path}"
    
    # Add query parameters if present (but exclude link_id as it's only for our proxy)
    query_params = {k: v for k, v in request.query_params.items() if k != "link_id"}
    if query_params:
        from urllib.parse import urlencode
        openedx_url += "?" + urlencode(query_params)
    
    # Create session with stored cookies
    session = requests.Session()
    session.cookies.set("lms_sessionid", user_token.access_token)
    session.cookies.set("sessionid", user_token.access_token)
    
    # Get CSRF token from cookies or headers
    # Priority: form data > headers > cookies
    csrf_token = None
    
    # First, try to get from form data (will be extracted later for multipart)
    # For now, get from headers or cookies
    csrf_token = (
        request.headers.get("x-csrftoken") or 
        request.headers.get("x-csrf-token") or
        request.cookies.get("csrftoken") or 
        request.cookies.get("edxcsrftoken")
    )
    
    # Set CSRF token in session cookies (Open edX needs this)
    if csrf_token:
        session.cookies.set("csrftoken", csrf_token)
        session.cookies.set("edxcsrftoken", csrf_token)
        logger.info(f"Using CSRF token from headers/cookies: {csrf_token[:20]}...")
    else:
        logger.warning("No CSRF token found in headers or cookies")
    
    try:
        # Get the content type from the request
        content_type = request.headers.get("content-type", "")
        
        logger.info(f"POST request to Open edX: {openedx_url}, Content-Type: {content_type}")
        
        # Prepare headers for forwarding
        headers = {
            "Referer": referer or f"{OPENEDX_API_BASE}/{path}",
            "User-Agent": request.headers.get("user-agent", "fastapi-edx-proxy/1.0"),
            "X-Requested-With": request.headers.get("x-requested-with", "XMLHttpRequest")
        }
        
        # Add CSRF token to headers if available
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            headers["X-CSRF-Token"] = csrf_token
        
        # Don't copy Content-Type header for multipart/form-data
        # Let requests library generate it with proper boundary
        # For other content types, copy it
        if content_type and "multipart/form-data" not in content_type:
            headers["Content-Type"] = content_type
        
        response = None
        
        # Handle different content types
        try:
            if "multipart/form-data" in content_type:
                # For multipart/form-data, forward the raw body to preserve the exact format
                # This ensures the boundary and all form fields are preserved exactly
                raw_body = await request.body()
                logger.info(f"Forwarding multipart form data as raw body ({len(raw_body)} bytes)")
                
                # Try to extract CSRF token from form data to ensure cookie matches
                # Parse the raw body to find csrfmiddlewaretoken
                try:
                    body_str = raw_body.decode('utf-8', errors='ignore')
                    # Look for csrfmiddlewaretoken in the multipart body
                    csrf_match = re.search(r'name="csrfmiddlewaretoken"\s*\r?\n\r?\n([^\r\n]+)', body_str)
                    if csrf_match:
                        form_csrf_token = csrf_match.group(1).strip()
                        # Update session cookie to match form token (Open edX requires both to match)
                        session.cookies.set("csrftoken", form_csrf_token)
                        session.cookies.set("edxcsrftoken", form_csrf_token)
                        headers["X-CSRFToken"] = form_csrf_token
                        headers["X-CSRF-Token"] = form_csrf_token
                        logger.info(f"Extracted CSRF token from form data: {form_csrf_token[:20]}...")
                except Exception as e:
                    logger.warning(f"Could not extract CSRF token from form data: {str(e)}")
                
                # Forward the raw body with the original Content-Type header (including boundary)
                headers["Content-Type"] = content_type
                response = session.post(
                    openedx_url,
                    data=raw_body,
                    headers=headers,
                    timeout=30,
                    allow_redirects=False
                )
            elif "application/x-www-form-urlencoded" in content_type:
                # Handle URL-encoded form data
                form_data = await request.form()
                form_dict = {}
                
                # Convert FormData to dict, handling list values properly
                # FastAPI's FormData can return lists, but we need to flatten single values
                for key, value in form_data.items():
                    if isinstance(value, list):
                        # If it's a list with one item, use that item; otherwise keep as list
                        form_dict[key] = value[0] if len(value) == 1 else value
                    else:
                        form_dict[key] = value
                
                logger.info(f"Forwarding form-urlencoded data with {len(form_dict)} fields")
                
                # Extract CSRF token from form data to ensure cookie matches
                # Django requires the csrfmiddlewaretoken in form to match the cookie
                form_csrf_token = form_dict.get("csrfmiddlewaretoken")
                if form_csrf_token:
                    # Ensure it's a string, not a list
                    if isinstance(form_csrf_token, list):
                        form_csrf_token = form_csrf_token[0] if form_csrf_token else None
                    
                    if form_csrf_token:
                        # Update session cookie to match form token (Open edX requires both to match)
                        session.cookies.set("csrftoken", form_csrf_token)
                        session.cookies.set("edxcsrftoken", form_csrf_token)
                        headers["X-CSRFToken"] = form_csrf_token
                        headers["X-CSRF-Token"] = form_csrf_token
                        logger.info(f"Extracted CSRF token from form-urlencoded data: {form_csrf_token[:20]}...")
                
                # Forward the POST request with form data
                response = session.post(
                    openedx_url,
                    data=form_dict,
                    headers=headers,
                    timeout=30,
                    allow_redirects=False
                )
            else:
                # Handle JSON or other content types
                try:
                    body = await request.json()
                    headers["Content-Type"] = "application/json"
                    logger.info(f"Forwarding JSON data")
                    response = session.post(
                        openedx_url,
                        json=body,
                        headers=headers,
                        timeout=30,
                        allow_redirects=False
                    )
                except Exception as json_error:
                    # Fallback: try as form data
                    logger.info(f"JSON parsing failed, trying as form data: {str(json_error)}")
                    form_data = await request.form()
                    form_dict = {}
                    
                    # Convert FormData to dict, handling list values properly
                    for key, value in form_data.items():
                        if isinstance(value, list):
                            form_dict[key] = value[0] if len(value) == 1 else value
                        else:
                            form_dict[key] = value
                    
                    # Extract CSRF token from form data if present
                    form_csrf_token = form_dict.get("csrfmiddlewaretoken")
                    if form_csrf_token:
                        # Ensure it's a string, not a list
                        if isinstance(form_csrf_token, list):
                            form_csrf_token = form_csrf_token[0] if form_csrf_token else None
                        
                        if form_csrf_token:
                            # Update session cookie to match form token
                            session.cookies.set("csrftoken", form_csrf_token)
                            session.cookies.set("edxcsrftoken", form_csrf_token)
                            headers["X-CSRFToken"] = form_csrf_token
                            headers["X-CSRF-Token"] = form_csrf_token
                            logger.info(f"Extracted CSRF token from fallback form data: {form_csrf_token[:20]}...")
                    
                    response = session.post(
                        openedx_url,
                        data=form_dict,
                        headers=headers,
                        timeout=30,
                        allow_redirects=False
                    )
        except Exception as form_error:
            logger.error(f"Error parsing form data: {str(form_error)}", exc_info=True)
            # Try to get raw body and forward it
            try:
                body = await request.body()
                logger.info(f"Forwarding raw body ({len(body)} bytes)")
                
                # Try to extract CSRF token from raw body if it's form-urlencoded
                content_type = request.headers.get("content-type", "")
                if "application/x-www-form-urlencoded" in content_type:
                    try:
                        body_str = body.decode('utf-8', errors='ignore')
                        parsed = parse_qs(body_str)
                        if "csrfmiddlewaretoken" in parsed:
                            form_csrf_token = parsed["csrfmiddlewaretoken"][0] if parsed["csrfmiddlewaretoken"] else None
                            if form_csrf_token:
                                session.cookies.set("csrftoken", form_csrf_token)
                                session.cookies.set("edxcsrftoken", form_csrf_token)
                                headers["X-CSRFToken"] = form_csrf_token
                                headers["X-CSRF-Token"] = form_csrf_token
                                logger.info(f"Extracted CSRF token from raw body: {form_csrf_token[:20]}...")
                    except Exception as csrf_extract_error:
                        logger.warning(f"Could not extract CSRF token from raw body: {str(csrf_extract_error)}")
                
                response = session.post(
                    openedx_url,
                    data=body,
                    headers=headers,
                    timeout=30,
                    allow_redirects=False
                )
            except Exception as body_error:
                logger.error(f"Error forwarding request: {str(body_error)}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"detail": f"Error processing request: {str(form_error)}"},
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                        "Access-Control-Allow-Headers": "*"
                    }
                )
        
        if response is None:
            logger.error(f"No response received from Open edX for {openedx_url}")
            return JSONResponse(
                status_code=500,
                content={"detail": "No response from Open edX"},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*"
                }
            )
        
        logger.info(f"POST proxy response status: {response.status_code} for {openedx_url}")
        
        # Log response content for debugging (first 500 chars)
        if response.text:
            response_preview = response.text[:500] if len(response.text) > 500 else response.text
            logger.info(f"Response content preview: {response_preview}")
        
        # Handle redirects
        if response.status_code in [301, 302, 303, 307, 308]:
            location = response.headers.get("Location", "")
            if location:
                # Intercept Learning MFE URLs (localhost:2000) and convert to proxy
                # This prevents white screen issues in iframe embedding
                if LEARNING_MFE_URL in location or "localhost:2000" in location or ":2000" in location:
                    # Extract the path from Learning MFE URL
                    from urllib.parse import urlparse, urlunparse
                    parsed = urlparse(location)
                    mfe_path = parsed.path
                    mfe_query = parsed.query
                    
                    # Check if the original request was for courseware (to detect loops)
                    current_path = path.lower()
                    is_courseware_request = "/courseware" in current_path
                    
                    if "/course/" in mfe_path:
                        # Extract course ID from path like /course/course-v1:org+course+run
                        course_match = re.search(r'/course/([^/]+)', mfe_path)
                        if course_match:
                            course_id = course_match.group(1)
                            
                            # If this is a courseware request redirecting to Learning MFE, avoid loop
                            if is_courseware_request:
                                # Return HTML that embeds Learning MFE directly to avoid redirect loop
                                mfe_url = f"{LEARNING_MFE_URL}/course/{course_id}"
                                html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Course Content</title>
    <style>
        body, html {{ margin: 0; padding: 0; height: 100%; overflow: hidden; }}
        iframe {{ width: 100%; height: 100vh; border: none; }}
    </style>
</head>
<body>
    <iframe src="{mfe_url}" allow="fullscreen" allowfullscreen></iframe>
</body>
</html>
"""
                                html_response = HTMLResponse(content=html_content, status_code=200)
                                forward_cookies_from_response(response, html_response, link_id)
                                logger.info(f"Detected redirect loop for courseware in POST, returning iframe with Learning MFE: {mfe_url}")
                                return html_response
                            
                            # For non-courseware requests, redirect to course about page instead of courseware
                            location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/courses/{course_id}/about"
                        else:
                            # Fallback: redirect to dashboard
                            location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard"
                    else:
                        # For other Learning MFE paths, redirect to dashboard
                        location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard"
                    
                    if "?" in location:
                        location += f"&link_id={link_id}"
                    else:
                        location += f"?link_id={link_id}"
                    logger.info(f"Intercepted Learning MFE redirect in POST, converted to proxy URL: {location}")
                
                # Convert Open edX URL to proxy URL if needed
                elif location.startswith(OPENEDX_API_BASE):
                    location = location.replace(OPENEDX_API_BASE, f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy")
                    if "?" in location:
                        location += f"&link_id={link_id}"
                    else:
                        location += f"?link_id={link_id}"
                elif location.startswith("/") and not location.startswith("/openedx-proxy/"):
                    location = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{location}"
                    if "?" in location:
                        location += f"&link_id={link_id}"
                    else:
                        location += f"?link_id={link_id}"
                
                redirect_response = RedirectResponse(url=location, status_code=response.status_code)
                # Forward cookies from Open edX response
                forward_cookies_from_response(response, redirect_response, link_id)
                return redirect_response
        
        # Handle enrollment responses that return a URL path in the body (not a redirect header)
        # Open edX enrollment endpoint returns 200 with a URL path like "/course_modes/choose/..."
        content_type_response = response.headers.get("content-type", "")
        response_text = response.text.strip() if response.text else ""
        
        # Handle enrollment responses that return a URL path in the body
        # Open edX enrollment endpoint returns 200 with a URL path like "/course_modes/choose/..."
        # We need to convert these to proxy URLs so the frontend navigates correctly
        logger.info(f"Checking for URL path conversion: status={response.status_code}, text_len={len(response_text)}, starts_with_slash={response_text.startswith('/') if response_text else False}, content_type={content_type_response}")
        
        if (response.status_code == 200 and 
            response_text and 
            response_text.startswith("/") and 
            len(response_text) < 500 and
            not response_text.startswith("<") and  # Not HTML
            "\n" not in response_text and  # Not multi-line (single line URL path)
            not response_text.startswith("/openedx-proxy/") and  # Not already proxied
            not response_text.startswith("http")):  # Not full URL
            
            logger.info(f"✓ Detected URL path in enrollment response: {response_text}")
            
            # Convert to proxy URL
            proxy_url = f"{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{response_text}"
            # Add link_id to the URL
            if "?" in proxy_url:
                proxy_url += f"&link_id={link_id}"
            else:
                proxy_url += f"?link_id={link_id}"
            
            logger.info(f"Converted enrollment redirect URL: {response_text} -> {proxy_url}")
            
            # Return the full proxy URL as plain text (same format as Open edX, but full URL)
            # Frontend JavaScript will navigate to this URL
            text_response = Response(
                content=proxy_url.encode('utf-8'),
                status_code=200,
                media_type="text/plain",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*"
                }
            )
            # Forward cookies from Open edX response
            forward_cookies_from_response(response, text_response, link_id)
            return text_response
        
        # For JSON responses (like enrollment API)
        if "application/json" in content_type_response:
            try:
                json_response = response.json()
                logger.info(f"Returning JSON response: {json_response}")
                json_fastapi_response = JSONResponse(
                    content=json_response,
                    status_code=response.status_code,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                        "Access-Control-Allow-Headers": "*"
                    }
                )
                # Forward cookies from Open edX response
                forward_cookies_from_response(response, json_fastapi_response, link_id)
                return json_fastapi_response
            except Exception as json_error:
                logger.warning(f"Failed to parse JSON response: {str(json_error)}, returning text")
                # Return as text if JSON parsing fails
                text_fastapi_response = Response(
                    content=response.text.encode('utf-8') if response.text else b"",
                    status_code=response.status_code,
                    media_type="text/plain",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                        "Access-Control-Allow-Headers": "*"
                    }
                )
                # Forward cookies from Open edX response
                forward_cookies_from_response(response, text_fastapi_response, link_id)
                return text_fastapi_response
        
        # For HTML responses (form submissions that return HTML)
        if "text/html" in content_type_response:
            content = response.text if response.text else ""
            
            # Replace Learning MFE URLs (localhost:2000) with proxy URLs to prevent iframe issues
            # This ensures all navigation stays within the proxy context
            content = re.sub(
                rf'{re.escape(LEARNING_MFE_URL)}/course/([^"\s\'<>]+)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/courses/\1/courseware?link_id={link_id}',
                content
            )
            content = re.sub(
                rf'{re.escape(LEARNING_MFE_URL)}([^"\s\'<>]*)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard?link_id={link_id}',
                content
            )
            # Also catch any localhost:2000 references
            content = re.sub(
                r'http://localhost:2000([^"\s\'<>]*)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard?link_id={link_id}',
                content
            )
            content = re.sub(
                r'https://localhost:2000([^"\s\'<>]*)',
                rf'{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy/dashboard?link_id={link_id}',
                content
            )
            
            # Replace relative URLs with proxy URLs
            def add_link_id_to_href_post(match):
                url_path = match.group(1)
                if '?' in url_path or url_path.startswith('http'):
                    return match.group(0)
                # For asset URLs and static files, use static proxy (no link_id needed)
                if url_path.startswith('asset-v1:') or url_path.startswith('static/'):
                    return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-static{url_path}"'
                separator = '&' if '?' in url_path else '?'
                return f'href="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            def add_link_id_to_action(match):
                url_path = match.group(1)
                if '?' in url_path or url_path.startswith('http'):
                    return match.group(0)
                separator = '&' if '?' in url_path else '?'
                return f'action="{FASTAPI_PUBLIC_BASE_URL}/openedx-proxy{url_path}{separator}link_id={link_id}"'
            
            content = re.sub(r'href="(/[^"]*)"', add_link_id_to_href_post, content)
            content = re.sub(r'action="(/[^"]*)"', add_link_id_to_action, content)
            # For src, use static proxy (handle asset URLs too)
            def replace_src_post(match):
                url_path = match.group(1)
                if url_path.startswith('http'):
                    return match.group(0)
                return f'src="{FASTAPI_PUBLIC_BASE_URL}/openedx-static{url_path}"'
            content = re.sub(r'src="(/[^"]*)"', replace_src_post, content)
            
            if '<head>' in content:
                content = content.replace('<head>', f'<head><base href="{OPENEDX_API_BASE}/">')
            
            html_response = HTMLResponse(content=content, status_code=response.status_code)
            
            # Forward all cookies (CSRF and session) from Open edX response
            forward_cookies_from_response(response, html_response, link_id)
            
            return html_response
        
        # For other content types, return as-is
        response_content = response.content if response.content else b""
        if not response_content and response.text:
            response_content = response.text.encode('utf-8')
        
        other_response = Response(
            content=response_content,
            status_code=response.status_code,
            media_type=content_type_response or "application/octet-stream",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
        # Forward cookies from Open edX response
        forward_cookies_from_response(response, other_response, link_id)
        return other_response
            
    except requests.exceptions.Timeout as e:
        logger.error(f"POST proxy request timeout: {str(e)} for {openedx_url}")
        return JSONResponse(
            status_code=504,
            content={"detail": "Request timeout"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
    except requests.exceptions.ConnectionError as e:
        logger.error(f"POST proxy connection error: {str(e)} for {openedx_url}")
        return JSONResponse(
            status_code=502,
            content={"detail": "Connection error"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"POST proxy request failed: {str(e)} for {openedx_url}", exc_info=True)
        return JSONResponse(
            status_code=502,
            content={"detail": f"Proxy request failed: {str(e)}"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )
    except Exception as e:
        logger.error(f"Unexpected error in POST proxy: {str(e)} for {openedx_url}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal error: {str(e)}"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*"
            }
        )

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
        # Use SameSite=None for cross-origin iframe embedding
        response.set_cookie(
            key="edx_link_id",
            value=link_id,
            path="/",
            httponly=False,  # Allow JavaScript to read it if needed
            samesite="none",  # Changed to "none" for cross-origin
            secure=False,  # Set to False for HTTP, True for HTTPS
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
            # Use SameSite=None for cross-origin iframe embedding
            response.set_cookie(
                key="edx_link_id",
                value=link_id,
                path="/",
                httponly=False,
                samesite="none",  # Changed to "none" for cross-origin
                secure=False,  # Set to False for HTTP, True for HTTPS
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
        
        # Process payload: convert relative URLs to absolute URLs
        processed_payload = payload.copy()
        
        # Convert certificatePdfUrl from relative to absolute URL if needed
        if processed_payload.get('certificatePdfUrl'):
            cert_pdf_url = processed_payload['certificatePdfUrl']
            if cert_pdf_url.startswith('/'):
                # It's a relative URL, convert to absolute
                processed_payload['certificatePdfUrl'] = f"{OPENEDX_API_BASE}{cert_pdf_url}"
                logger.info(f"Converted certificatePdfUrl to absolute URL: {processed_payload['certificatePdfUrl']}")
        
        # Convert certificateUrl from relative to absolute URL if needed
        if processed_payload.get('certificateUrl'):
            cert_url = processed_payload['certificateUrl']
            if cert_url.startswith('/'):
                # It's a relative URL, convert to absolute
                processed_payload['certificateUrl'] = f"{OPENEDX_API_BASE}{cert_url}"
                logger.info(f"Converted certificateUrl to absolute URL: {processed_payload['certificateUrl']}")
        
        # Ensure courseName is present (use courseId as fallback)
        if not processed_payload.get('courseName'):
            # Extract course name from courseId if not provided
            course_id = processed_payload.get('courseId', '')
            if course_id:
                # Format: course-v1:test_organization+CS105+2025-12
                # Extract course name parts
                parts = course_id.split(':')
                if len(parts) > 1:
                    course_parts = parts[1].split('+')
                    # Remove the last part (year) and join the rest
                    course_name_parts = course_parts[:-1] if len(course_parts) > 1 else course_parts
                    processed_payload['courseName'] = ' '.join(course_name_parts).replace('_', ' ')
                    logger.info(f"Extracted courseName from courseId: {processed_payload['courseName']}")
        
        # Forward to ICG API
        icg_url = f"{ICG_API_BASE}{ICG_WEBHOOK_ENDPOINT}"
        logger.info(f"Forwarding webhook to ICG API: {icg_url}")
        logger.debug(f"Processed payload: {processed_payload}")
        
        response = requests.post(
            icg_url,
            json=processed_payload,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'FastAPI-edX-Webhook/1.0'
            },
            timeout=30
        )
        
        if response.status_code in [200, 201, 204]:
            logger.info(f"Successfully forwarded webhook to ICG API for user {processed_payload.get('username')}, course {processed_payload.get('courseId')}")
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
