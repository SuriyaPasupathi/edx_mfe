#!/bin/bash

# Start the Authoring MFE with correct configuration
# Uses environment variables from docker-compose.yml if available, otherwise defaults to localhost

echo "=========================================="
echo "Starting Authoring MFE"
echo "=========================================="
echo ""

# Use environment variables from docker-compose.yml if set, otherwise use localhost defaults
export STUDIO_BASE_URL=${STUDIO_BASE_URL:-http://localhost:18010}
export LMS_BASE_URL=${LMS_BASE_URL:-http://localhost:18000}
export BASE_URL=${BASE_URL:-http://localhost:2001}
export MFE_CONFIG_API_URL=${MFE_CONFIG_API_URL:-http://localhost:18000/api/mfe_config/v1}
export MFE_NAME=${MFE_NAME:-authoring}
export PUBLIC_PATH=${PUBLIC_PATH:-/authoring/}
export PORT=${PORT:-2001}

# Feature flags (use env vars if set, otherwise defaults)
export ENABLE_UNIT_PAGE=${ENABLE_UNIT_PAGE:-true}
export ENABLE_ASSETS_PAGE=${ENABLE_ASSETS_PAGE:-true}
export ENABLE_CERTIFICATE_PAGE=${ENABLE_CERTIFICATE_PAGE:-true}
export ENABLE_ACCESSIBILITY_PAGE=${ENABLE_ACCESSIBILITY_PAGE:-true}
export ENABLE_TAGGING_TAXONOMY_PAGES=${ENABLE_TAGGING_TAXONOMY_PAGES:-true}
export ENABLE_PROGRESS_GRAPH_SETTINGS=${ENABLE_PROGRESS_GRAPH_SETTINGS:-true}

# Additional config (use env vars if set)
export TERMS_OF_SERVICE_URL=${TERMS_OF_SERVICE_URL:-}
export PRIVACY_POLICY_URL=${PRIVACY_POLICY_URL:-}
export SUPPORT_URL=${SUPPORT_URL:-https://support.openedx.org}
export LEARNING_BASE_URL=${LEARNING_BASE_URL:-}

echo "Configuration:"
echo "  Studio (CMS):  $STUDIO_BASE_URL"
echo "  LMS:           $LMS_BASE_URL"
echo "  MFE:           $BASE_URL"
echo "  MFE Config API: $MFE_CONFIG_API_URL"
echo "  MFE Name:      $MFE_NAME"
echo "  Public Path:   $PUBLIC_PATH"
echo "  Port:          $PORT"
echo ""
echo "=========================================="
echo ""

# Start the MFE
npm start

