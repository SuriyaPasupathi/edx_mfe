#!/bin/bash

# Start the Learner Dashboard MFE with correct local configuration
# This ensures the MFE connects to LMS on port 18000

echo "=========================================="
echo "Starting Learner Dashboard MFE"
echo "=========================================="
echo ""
echo "Configuration:"
echo "  LMS:                  http://localhost:18000"
echo "  Studio (CMS):         http://localhost:18010"
echo "  Learning MFE:         http://localhost:2000"
echo "  Learner Dashboard:    http://localhost:1996"
echo ""
echo "MFE Config API: http://localhost:18000/api/mfe_config/v1"
echo ""
echo "=========================================="
echo ""

# Set environment variables and start
export BASE_URL=http://localhost:1996
export LMS_BASE_URL=http://localhost:18000
export STUDIO_BASE_URL=http://localhost:18010
export MARKETING_SITE_BASE_URL=http://localhost:18000
export MFE_CONFIG_API_URL=http://localhost:18000/api/mfe_config/v1
export PUBLIC_PATH=/learner-dashboard/
export PORT=1996

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

# Learning MFE integration
export LEARNING_BASE_URL=http://localhost:2000

# Feature flags
export ENABLE_NOTICES=true
export EXPERIMENT_08_23_VAN_PAINTED_DOOR=true

# Contact and support
export CONTACT_URL=http://localhost:18000/contact
export SUPPORT_URL=http://localhost:18000/support

# Branding
export SITE_NAME=edX
export LOGO_URL=http://localhost:18000/static/images/logo.png
export FAVICON_URL=http://localhost:18000/favicon.ico

# Legal URLs
export TERMS_OF_SERVICE_URL=http://localhost:18000/terms-of-service
export PRIVACY_POLICY_URL=http://localhost:18000/privacy-policy

# Account settings URLs
export ACCOUNT_SETTINGS_URL=http://localhost:1997
export ACCOUNT_PROFILE_URL=http://localhost:1995

# Data API
export DATA_API_BASE_URL=http://localhost:18000

# Start the MFE
npm start

