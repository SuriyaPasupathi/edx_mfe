#!/bin/bash

# Start the Authoring MFE with correct local configuration
# This ensures the MFE connects to Studio on port 18010

echo "=========================================="
echo "Starting Authoring MFE with Local Config"
echo "=========================================="
echo ""
echo "Configuration:"
echo "  Studio (CMS):  http://localhost:18010"
echo "  LMS:           http://localhost:18000"
echo "  MFE:           http://localhost:2001"
echo ""
echo "MFE Config API: http://localhost:18010/api/mfe_config/v1"
echo ""
echo "=========================================="
echo ""

# Set environment variables and start
export STUDIO_BASE_URL=http://localhost:18010
export LMS_BASE_URL=http://localhost:18000
export BASE_URL=http://localhost:2001
export MFE_CONFIG_API_URL=http://localhost:18010/api/mfe_config/v1
export PUBLIC_PATH=/
export PORT=2001

# Feature flags
export ENABLE_UNIT_PAGE=true
export ENABLE_ASSETS_PAGE=true
export ENABLE_CERTIFICATE_PAGE=true
export ENABLE_ACCESSIBILITY_PAGE=true
export ENABLE_TAGGING_TAXONOMY_PAGES=true
export ENABLE_PROGRESS_GRAPH_SETTINGS=true

# Start the MFE
npm start

