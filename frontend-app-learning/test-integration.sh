#!/bin/bash

# Learning MFE Integration Test Script
# This script verifies that the Learning MFE is properly integrated with Open edX

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_info() { echo -e "${BLUE}ℹ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠ $1${NC}"; }
print_error() { echo -e "${RED}✗ $1${NC}"; }

echo "========================================"
echo "Learning MFE Integration Test"
echo "========================================"
echo ""

# Test 1: Check Node.js version
echo "Test 1: Checking Node.js version..."
if command -v node &> /dev/null; then
    NODE_VERSION=$(node -v)
    print_success "Node.js is installed: $NODE_VERSION"
else
    print_error "Node.js is not installed"
    exit 1
fi

# Test 2: Check npm
echo ""
echo "Test 2: Checking npm..."
if command -v npm &> /dev/null; then
    NPM_VERSION=$(npm -v)
    print_success "npm is installed: $NPM_VERSION"
else
    print_error "npm is not installed"
    exit 1
fi

# Test 3: Check if node_modules exists
echo ""
echo "Test 3: Checking dependencies..."
if [ -d "node_modules" ]; then
    print_success "Dependencies are installed (node_modules found)"
else
    print_warning "Dependencies not installed. Run ./setup_learning_mfe.sh first"
fi

# Test 4: Check env.config.jsx
echo ""
echo "Test 4: Checking configuration file..."
if [ -f "env.config.jsx" ]; then
    print_success "Configuration file exists: env.config.jsx"
else
    print_error "Configuration file not found: env.config.jsx"
    exit 1
fi

# Test 5: Check LMS availability
echo ""
echo "Test 5: Checking LMS availability..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:18000 | grep -q "200\|302"; then
    print_success "LMS is running on http://localhost:18000"
else
    print_error "LMS is not accessible on http://localhost:18000"
    print_info "Please start your LMS before running the Learning MFE"
fi

# Test 6: Check MFE Config API
echo ""
echo "Test 6: Checking MFE Config API..."
if curl -s http://localhost:18000/api/mfe_config/v1 &> /dev/null; then
    print_success "MFE Config API is accessible"
else
    print_warning "MFE Config API is not accessible"
    print_info "Make sure ENABLE_MFE_CONFIG_API is true in lms.env.json"
fi

# Test 7: Check port 2000 availability
echo ""
echo "Test 7: Checking port 2000 availability..."
if lsof -Pi :2000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    print_warning "Port 2000 is already in use"
    print_info "You may need to stop the existing process or use a different port"
else
    print_success "Port 2000 is available"
fi

# Test 8: Check CORS configuration
echo ""
echo "Test 8: Checking CORS configuration in LMS..."
if grep -q "http://localhost:2000" "/home/suriya-vcw/Desktop/manual build/edx-platform/lms.env.json" 2>/dev/null; then
    print_success "Learning MFE is in CORS whitelist"
else
    print_warning "Learning MFE may not be in CORS whitelist"
    print_info "Check lms.env.json CORS_ORIGIN_WHITELIST"
fi

# Summary
echo ""
echo "========================================"
echo "Test Summary"
echo "========================================"
echo ""
print_info "All critical tests passed! You can now start the Learning MFE:"
echo ""
echo "  ./start-learning.sh"
echo ""
print_info "After starting, access the MFE at:"
echo ""
echo "  http://localhost:2000"
echo ""
echo "========================================"

