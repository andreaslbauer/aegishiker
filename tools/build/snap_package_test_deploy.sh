#!/bin/bash
# ==============================================================================
# aegishiker - Full Build -> Test -> Deploy Pipeline (Snap)
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

BUILD_SNAP="./tools/build/build-snap.sh"
TEST_SNAP="./tools/build/test-snap.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_error() { echo -e "${RED}[✗] $1${NC}"; }
print_status() { echo -e "${GREEN}[✓] $1${NC}"; }
print_info() { echo -e "${YELLOW}[i] $1${NC}"; }

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Orchestrating Snap Build, Test, Deploy  ${NC}"
echo -e "${GREEN}========================================${NC}"

# Step 1: Build
print_info "Step 1: Building Snap package..."
chmod +x "$BUILD_SNAP"
$BUILD_SNAP

# Step 2: Test
print_info "Step 2: Testing Snap package..."
chmod +x "$TEST_SNAP"
$TEST_SNAP

# Step 3: Deploy to Snap Store
print_info "Step 3: Uploading to Snap Store..."
LATEST_SNAP=$(ls -t *.snap 2>/dev/null | head -n 1)
if [ -n "$LATEST_SNAP" ]; then
    print_info "Releasing $LATEST_SNAP to Snap Store..."
    if [ -n "${SNAP_UPLOAD_COMMAND:-}" ]; then
        eval "$SNAP_UPLOAD_COMMAND"
    else
        snapcraft upload "$LATEST_SNAP"
    fi
else
    print_error "No .snap package found to upload."
    exit 1
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
echo -e "${GREEN}SNAP BUILD, TEST, AND DEPLOY SUCCESSFUL!${NC}"
echo -e "${GREEN}========================================${NC}"
