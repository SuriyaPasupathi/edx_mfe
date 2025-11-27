#!/bin/bash

# Frontend App Authoring - Setup Script
# This script sets up the authoring MFE for local development with Open edX

set -e  # Exit on error

echo "=========================================="
echo "Frontend App Authoring - Setup Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

# Check if running from the correct directory
if [ ! -f "package.json" ]; then
    print_error "Error: package.json not found. Please run this script from the frontend-app-authoring directory."
    exit 1
fi

# Step 1: Check Node.js version
echo "Step 1: Checking Node.js version..."
if ! command -v node &> /dev/null; then
    print_error "Node.js is not installed. Please install Node.js 20 or use nvm."
    exit 1
fi

NODE_VERSION=$(node -v | cut -d 'v' -f 2 | cut -d '.' -f 1)
REQUIRED_VERSION=20

if [ -f ".nvmrc" ]; then
    print_info "Using Node.js version from .nvmrc file"
    if command -v nvm &> /dev/null; then
        nvm use
    else
        print_info "NVM not found. Please ensure you're using Node.js $(cat .nvmrc)"
    fi
fi

if [ "$NODE_VERSION" -lt "$REQUIRED_VERSION" ]; then
    print_error "Node.js version $REQUIRED_VERSION or higher is required. Current version: $(node -v)"
    exit 1
fi

print_success "Node.js version: $(node -v)"

# Step 2: Check npm
echo ""
echo "Step 2: Checking npm..."
if ! command -v npm &> /dev/null; then
    print_error "npm is not installed."
    exit 1
fi
print_success "npm version: $(npm -v)"

# Step 3: Clean previous installations (optional)
echo ""
echo "Step 3: Cleaning previous installations..."
if [ -d "node_modules" ]; then
    print_info "Removing existing node_modules..."
    rm -rf node_modules
    print_success "Removed node_modules"
fi

if [ -f "package-lock.json" ]; then
    print_info "Keeping package-lock.json for consistent installations"
fi

# Step 4: Install dependencies
echo ""
echo "Step 4: Installing npm dependencies..."
print_info "This may take several minutes..."
npm ci
print_success "Dependencies installed successfully"

# Step 5: Check for env.config.jsx
echo ""
echo "Step 5: Checking environment configuration..."
if [ ! -f "env.config.jsx" ]; then
    print_error "env.config.jsx not found!"
    if [ -f "example.env.config.jsx" ]; then
        print_info "Creating env.config.jsx from example.env.config.jsx"
        cp example.env.config.jsx env.config.jsx
        print_success "Created env.config.jsx"
        print_info "Please review and update env.config.jsx with your local configuration"
    else
        print_error "No configuration file found. Please create env.config.jsx"
        exit 1
    fi
else
    print_success "env.config.jsx exists"
fi

# Step 6: Display Open edX platform requirements
echo ""
echo "=========================================="
echo "Open edX Platform Requirements"
echo "=========================================="
print_info "Make sure your Open edX platform has the following configuration:"
echo ""
echo "  1. In cms.env.yml (Studio):"
echo "     - ENABLE_COURSE_AUTHORING_MICROFRONTEND: true"
echo "     - ENABLE_MFE_CONFIG_API: true"
echo "     - CORS_ORIGIN_WHITELIST includes http://localhost:2001"
echo ""
echo "  2. LMS and Studio should be running:"
echo "     - LMS: http://localhost:18000"
echo "     - Studio: http://localhost:18010"
echo ""
echo "  3. Run migrations if needed:"
echo "     cd ../edx-platform"
echo "     python manage.py lms migrate"
echo "     python manage.py cms migrate"
echo ""

# Step 7: Setup complete
echo "=========================================="
print_success "Setup completed successfully!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "  1. Start your Open edX platform (LMS and Studio)"
echo "  2. Start the authoring MFE:"
echo "     npm start"
echo ""
echo "  Or for development mode:"
echo "     npm run dev"
echo ""
echo "  The MFE will be available at:"
echo "     http://localhost:2001"
echo ""
echo "  You can access it through Studio by navigating to:"
echo "     http://localhost:18010"
echo ""
echo "=========================================="

