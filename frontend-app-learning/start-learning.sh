#!/bin/bash

# Start the Learning MFE with correct local configuration
# This ensures the MFE connects to LMS on port 18000

echo "=========================================="
echo "Starting Learning MFE with Local Config"
echo "=========================================="
echo ""
echo "Configuration:"
echo "  LMS:           http://localhost:18000"
echo "  Studio (CMS):  http://localhost:18010"
echo "  Learning MFE:  http://localhost:2000"
echo ""
echo "MFE Config API: http://localhost:18000/api/mfe_config/v1"
echo ""
echo "=========================================="
echo ""

# Set environment variables and start
export BASE_URL=http://localhost:2000
export LMS_BASE_URL=http://localhost:18000
export STUDIO_BASE_URL=http://localhost:18010
export MARKETING_SITE_BASE_URL=http://localhost:18000
export MFE_CONFIG_API_URL=http://localhost:18000/api/mfe_config/v1
export PUBLIC_PATH=/
export PORT=2000

# Authentication URLs
export LOGIN_URL=http://localhost:18000/login
export LOGOUT_URL=http://localhost:18000/logout
export REFRESH_ACCESS_TOKEN_ENDPOINT=http://localhost:18000/login_refresh

# Cookie configuration
export ACCESS_TOKEN_COOKIE_NAME=edx-jwt-cookie-header-payload
export USER_INFO_COOKIE_NAME=edx-user-info
export LANGUAGE_PREFERENCE_COOKIE_NAME=openedx-language-preference

# CSRF
export CSRF_TOKEN_API_PATH=/csrf/api/v1/token

# Feature flags
export ENABLE_JUMPNAV=true
# Disable notices if the notices plugin is not installed on the backend
# Set to true only if you have installed the platform-plugin-notices plugin
export ENABLE_NOTICES=false
export ENABLE_LEARNER_NOTES=true
export SHOW_UNGRADED_ASSIGNMENT_PROGRESS=false

# Contact and support
export CONTACT_URL=http://localhost:18000/contact
export SUPPORT_URL=https://support.openedx.org

# Branding
export SITE_NAME=edX
export LOGO_URL=http://localhost:18000/static/images/logo.png
export FAVICON_URL=http://localhost:18000/favicon.ico

# Legal URLs
export TERMS_OF_SERVICE_URL=http://localhost:18000/terms
export PRIVACY_POLICY_URL=http://localhost:18000/privacy

# Social Media
export TWITTER_URL=https://twitter.com/openedx
export TWITTER_HASHTAG=OpenEdX

# Start the MFE
npm start





