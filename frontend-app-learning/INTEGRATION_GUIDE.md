# Learning MFE Integration Guide for Open edX

This guide will help you integrate the **Learning MFE** (Micro Frontend) with your manually built Open edX platform.

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Installation Steps](#installation-steps)
4. [Configuration](#configuration)
5. [Starting the MFE](#starting-the-mfe)
6. [Troubleshooting](#troubleshooting)
7. [Testing the Integration](#testing-the-integration)

---

## Overview

The Learning MFE is the primary learner-facing application that renders:
- Course outlines and navigation
- Course content and units
- Progress tracking
- Course discussions
- Certificates and achievements

This MFE runs on **port 2000** by default and communicates with your LMS on port 18000.

---

## Prerequisites

Before starting, ensure you have:

- ✅ **Node.js 20.x** (check with `node -v`)
- ✅ **npm 9.x or later** (check with `npm -v`)
- ✅ **Open edX Platform** running on:
  - LMS: http://localhost:18000
  - Studio: http://localhost:18010
- ✅ **LMS and CMS properly configured** for MFE integration

---

## Installation Steps

### Step 1: Navigate to the Learning MFE Directory

```bash
cd "/home/suriya-vcw/Desktop/manual build/frontend-app-learning"
```

### Step 2: Run the Setup Script

The setup script will:
- Check Node.js version
- Install dependencies
- Verify configuration files
- Display setup instructions

```bash
./setup_learning_mfe.sh
```

**What this does:**
- Installs all npm dependencies (`npm ci`)
- Creates `env.config.jsx` if it doesn't exist
- Validates your environment

### Step 3: Verify Configuration Files

The Learning MFE uses `env.config.jsx` for runtime configuration. This file should already be present with the correct settings:

```jsx
// env.config.jsx highlights:
LMS_BASE_URL: 'http://localhost:18000',
STUDIO_BASE_URL: 'http://localhost:18010',
BASE_URL: 'http://localhost:2000',
LOGIN_URL: 'http://localhost:18000/login',
LOGOUT_URL: 'http://localhost:18000/logout',
ENABLE_JUMPNAV: 'true',
```

---

## Configuration

### A. LMS Configuration (edx-platform/lms.env.json)

Your LMS configuration has been updated to include:

```yaml
FEATURES:
  ENABLE_LEARNING_MICROFRONTEND: true
  ENABLE_MFE_CONFIG_API: true

CORS_ORIGIN_WHITELIST:
  - "http://localhost:2000"
  - "http://127.0.0.1:2000"

MFE_CONFIG_OVERRIDES:
  learning:
    BASE_URL: "http://localhost:2000"
    LMS_BASE_URL: "http://localhost:18000"
    STUDIO_BASE_URL: "http://localhost:18010"
    ENABLE_JUMPNAV: "true"
```

### B. Restart LMS to Apply Changes

After updating the configuration, restart your LMS:

```bash
cd "/home/suriya-vcw/Desktop/manual build/edx-platform"

# Stop LMS
./stop-lms.sh

# Start LMS
./start-lms.sh
```

Wait for the LMS to fully start (check http://localhost:18000).

---

## Starting the MFE

### Option 1: Using the Start Script (Recommended)

```bash
cd "/home/suriya-vcw/Desktop/manual build/frontend-app-learning"
./start-learning.sh
```

This script:
- Sets all required environment variables
- Configures feature flags
- Starts the development server on port 2000

### Option 2: Manual Start

```bash
npm start
```

**The Learning MFE will be available at:**
```
http://localhost:2000
```

---

## Accessing Course Content

### How Users Access Courses Through the Learning MFE

1. **Navigate to LMS Dashboard:**
   ```
   http://localhost:18000/dashboard
   ```

2. **Click on any enrolled course** - The LMS will automatically redirect to the Learning MFE:
   ```
   http://localhost:2000/course/course-v1:edX+Demo+2024
   ```

3. **Direct Course Access:**
   ```
   http://localhost:2000/course/<course_id>
   ```

### URL Structure

The Learning MFE handles these routes:
- `/course/:courseId` - Course outline and content
- `/course/:courseId/home` - Course home page
- `/course/:courseId/progress` - Progress page
- `/course/:courseId/dates` - Important dates
- `/course/:courseId/discussion` - Course discussions

---

## Troubleshooting

### Issue 1: MFE Shows "Invalid Host Header"

**Solution:** Make sure your LMS is running on `localhost:18000`. If you're using a different hostname, update `env.config.jsx`:

```jsx
LMS_BASE_URL: 'http://your-hostname:18000',
```

### Issue 2: Authentication Issues / Login Loops

**Symptoms:** 
- Redirected to login repeatedly
- "You are not authenticated" errors

**Solution:** Verify session configuration in `lms.env.json`:

```yaml
SESSION_COOKIE_SECURE: false
CSRF_COOKIE_SECURE: false
SESSION_COOKIE_SAMESITE: "Lax"
SESSION_COOKIE_HTTPONLY: false
```

Then restart LMS.

### Issue 3: CORS Errors in Browser Console

**Symptoms:**
```
Access to XMLHttpRequest at 'http://localhost:18000/api/...' from origin 
'http://localhost:2000' has been blocked by CORS policy
```

**Solution:** Check `lms.env.json` includes Learning MFE in CORS whitelist:

```yaml
CORS_ORIGIN_WHITELIST:
  - "http://localhost:2000"
  - "http://127.0.0.1:2000"
```

Restart LMS after changes.

### Issue 4: MFE Config API Not Working

**Test the API:**
```bash
curl http://localhost:18000/api/mfe_config/v1
```

**Expected Response:** JSON configuration object

**If it fails:**
1. Verify `ENABLE_MFE_CONFIG_API: true` in `lms.env.json`
2. Restart LMS
3. Check LMS logs for errors

### Issue 5: Dependencies Installation Fails

**Solution:**
```bash
# Clean install
rm -rf node_modules package-lock.json
npm install

# Or use the setup script
./setup_learning_mfe.sh
```

### Issue 6: Port 2000 Already in Use

**Find what's using the port:**
```bash
lsof -i :2000
```

**Kill the process or change port:**
```bash
export PORT=2005
npm start
```

Then update `BASE_URL` in `env.config.jsx` accordingly.

---

## Testing the Integration

### Test Checklist

- [ ] **MFE Starts Successfully**
  ```bash
  cd "/home/suriya-vcw/Desktop/manual build/frontend-app-learning"
  ./start-learning.sh
  ```
  Expected: Development server runs on http://localhost:2000

- [ ] **LMS is Running**
  ```bash
  curl http://localhost:18000
  ```
  Expected: HTML response

- [ ] **MFE Config API Works**
  ```bash
  curl http://localhost:18000/api/mfe_config/v1
  ```
  Expected: JSON configuration

- [ ] **Login Works**
  1. Go to http://localhost:18000/login
  2. Login with credentials
  3. Should redirect to dashboard

- [ ] **Course Access Works**
  1. Enroll in a course from LMS
  2. Click "View Course"
  3. Should load course in Learning MFE at http://localhost:2000

- [ ] **Course Navigation Works**
  - Course outline loads
  - Can click on units
  - Content displays properly
  - Progress updates

- [ ] **No Console Errors**
  - Open browser DevTools (F12)
  - Check Console tab for errors
  - Should see no CORS or authentication errors

---

## Development Tips

### Hot Reload

The MFE supports hot reload. Changes to source files will automatically refresh:

```bash
# Edit files in src/
# Browser automatically reloads
```

### Debugging

1. **Enable verbose logging:**
   ```bash
   export NODE_ENV=development
   npm start
   ```

2. **Check network requests:**
   - Open DevTools → Network tab
   - Filter by XHR/Fetch
   - Look for API calls to LMS

3. **Check browser storage:**
   - DevTools → Application tab
   - Check Cookies for authentication tokens
   - Check Local Storage for user preferences

### Environment Variables

Key environment variables (set in `start-learning.sh`):

```bash
LMS_BASE_URL=http://localhost:18000
STUDIO_BASE_URL=http://localhost:18010
BASE_URL=http://localhost:2000
PORT=2000
ENABLE_JUMPNAV=true
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    User Browser                         │
│  http://localhost:2000 (Learning MFE)                   │
└──────────────┬──────────────────────────────────────────┘
               │
               │ API Calls (CORS enabled)
               │
┌──────────────▼──────────────────────────────────────────┐
│           Open edX LMS (Django)                         │
│        http://localhost:18000                           │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ MFE Config API: /api/mfe_config/v1             │   │
│  │ Course API: /api/courses/v1/                   │   │
│  │ User API: /api/user/v1/                        │   │
│  │ Authentication: /login, /logout                │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  Database: MySQL (edxapp)                               │
│  MongoDB: Course content (edxapp)                       │
└─────────────────────────────────────────────────────────┘
```

---

## Additional Resources

### Documentation
- [Open edX MFE Documentation](https://openedx.github.io/frontend-platform/)
- [Learning MFE GitHub](https://github.com/openedx/frontend-app-learning)
- [Frontend Platform](https://github.com/openedx/frontend-platform)

### Configuration Files
- `env.config.jsx` - Runtime configuration
- `start-learning.sh` - Startup script with environment variables
- `setup_learning_mfe.sh` - Initial setup script

### Ports Reference
- **2000** - Learning MFE
- **2001** - Authoring MFE
- **2002** - Discussions MFE (if installed)
- **18000** - LMS
- **18010** - Studio (CMS)

---

## Quick Command Reference

```bash
# Setup (first time only)
cd "/home/suriya-vcw/Desktop/manual build/frontend-app-learning"
./setup_learning_mfe.sh

# Start Learning MFE
./start-learning.sh

# Alternative: npm start
npm start

# Build for production
npm run build

# Run tests
npm test

# Lint code
npm run lint

# Check for updates
npm outdated
```

---

## Success Indicators

Your Learning MFE is properly integrated when:

✅ MFE starts without errors on port 2000
✅ LMS is accessible on port 18000
✅ You can login through LMS
✅ Course links redirect to Learning MFE
✅ Course content loads correctly
✅ No CORS errors in browser console
✅ Navigation within courses works smoothly
✅ Progress tracking updates properly

---

## Need Help?

If you encounter issues:

1. **Check the logs:**
   - Browser console (F12)
   - Terminal where MFE is running
   - LMS logs in edx-platform

2. **Verify all services are running:**
   - LMS (port 18000)
   - Studio (port 18010)
   - Learning MFE (port 2000)

3. **Review configuration:**
   - `lms.env.json` has correct CORS settings
   - `env.config.jsx` has correct URLs
   - Feature flags are enabled

---

**Integration Complete!** 🎉

Your Learning MFE is now ready to use. Access it at http://localhost:2000 after starting the MFE and ensuring LMS is running.

