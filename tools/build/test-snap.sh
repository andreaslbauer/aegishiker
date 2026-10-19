#!/bin/bash
# ==============================================================================
# aegishiker - Automated Snap Package Verification & Test
# ==============================================================================

set -e

if [ "$EUID" -eq 0 ]; then
    echo "ERROR: Do not run this test script as root/sudo directly."
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PACKAGE_NAME="aegishiker"

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_info() { echo -e "${YELLOW}[i]${NC} $1"; }

# 1. Kill old processes and purge prior snap
pkill -f "$PACKAGE_NAME" || true
if snap list | grep -q "^$PACKAGE_NAME "; then
    print_info "Removing existing snap..."
    sudo snap remove --purge "$PACKAGE_NAME"
fi

# 2. Locate newest snap
LATEST_SNAP=$(ls -t ${PACKAGE_NAME}*.snap 2>/dev/null | head -n 1)
if [ -z "$LATEST_SNAP" ]; then
    print_error "No .snap file found. Build the snap first."
    exit 1
fi
print_status "Testing artifact: $LATEST_SNAP"

# 3. Install snap
print_info "Installing $LATEST_SNAP..."
sudo snap install --dangerous "$LATEST_SNAP"

# 4. Connect common plugs if declared
print_info "Connecting declared plugs..."
sudo snap connect "${PACKAGE_NAME}:removable-media" || true

# 5. Run test suite
if [ -d "tests" ]; then
    print_info "Running pytest suite..."
    export QT_QPA_PLATFORM=offscreen
    PY="$PROJECT_ROOT/.venv/bin/python3"
    [ -x "$PY" ] || PY="$(which python3)"
    "$PY" -m pytest tests -q --timeout=180 --timeout-method=thread || {
        print_error "Pytest suite failed."
        exit 1
    }
    print_status "Unit tests passed."
fi

# 6. Smoke-test installed binary
print_info "Launching installed snap smoke-test..."
${SNAP_SMOKE_TEST_COMMAND:-$PACKAGE_NAME --version}

print_status "Snap verification successful!"
