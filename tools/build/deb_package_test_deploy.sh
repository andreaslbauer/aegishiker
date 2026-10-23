#!/bin/bash
# ==============================================================================
# aegishiker - Full Build -> Test -> Deploy Pipeline (Debian)
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

BUILD_DEB="./tools/build/build-deb.sh"
TEST_DEB="./tools/build/test-deb.sh"
PACKAGE_NAME="aegishiker"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_error() { echo -e "${RED}[✗] $1${NC}"; }
print_status() { echo -e "${GREEN}[✓] $1${NC}"; }
print_info() { echo -e "${YELLOW}[i] $1${NC}"; }

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Orchestrating Build -> Test -> Deploy   ${NC}"
echo -e "${GREEN}========================================${NC}"

# Step 1: Build
print_info "Step 1: Building Debian package..."
chmod +x "$BUILD_DEB"
$BUILD_DEB "$@"

# Step 2: Test
print_info "Step 2: Testing Debian package..."
chmod +x "$TEST_DEB"
$TEST_DEB

# Step 3: Deploy (only reached if build and tests succeeded)
# Default: install the tested package on this machine. Override with DEPLOY_COMMAND="...".
print_info "Step 3: Deploying package..."
LATEST_DEB=$(ls -t dist/${PACKAGE_NAME}*.deb 2>/dev/null | head -n 1)
if [ -z "$LATEST_DEB" ]; then
    print_error "No .deb package found in dist/ to deploy."
    exit 1
fi
if [ -n "${DEPLOY_COMMAND:-}" ]; then
    eval "$DEPLOY_COMMAND"
else
    print_info "Installing $LATEST_DEB on this machine..."
    sudo dpkg -i "$LATEST_DEB" || sudo apt-get install -f -y
    INSTALLED_VERSION=$(dpkg-query -W -f='${Version}' "$PACKAGE_NAME" 2>/dev/null || true)
    if [ -z "$INSTALLED_VERSION" ]; then
        print_error "$PACKAGE_NAME is not installed after deploy."
        exit 1
    fi
    print_status "Deployed $PACKAGE_NAME $INSTALLED_VERSION locally."
fi

# Step 4: Bump patch version
if [ -f "version.txt" ]; then
    print_info "Step 4: Incrementing patch version in version.txt..."
    python3 -c "
with open('version.txt', 'r') as f:
    v = f.read().strip().split('.')
v[-1] = str(int(v[-1]) + 1)
new_v = '.'.join(v)
with open('version.txt', 'w') as f:
    f.write(new_v + '\n')
print(f'Bumped version to {new_v}')
"
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}BUILD, TEST, AND DEPLOY SUCCESSFUL!     ${NC}"
echo -e "${GREEN}========================================${NC}"
