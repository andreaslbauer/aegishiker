#!/bin/bash
# ==============================================================================
# aegishiker - Automated Debian Package Verification & Test
# ==============================================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PACKAGE_NAME="aegishiker"
APP_LOG_DIR="$HOME/.aegishiker/logs"
STDERR_LOG="$APP_LOG_DIR/launcher_stderr.log"

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_info() { echo -e "${YELLOW}[i]${NC} $1"; }

# 1. Purge previous installation
print_info "Purging prior installation of $PACKAGE_NAME..."
if dpkg -l | grep -q "^ii  $PACKAGE_NAME "; then
    sudo dpkg -P $PACKAGE_NAME
    print_status "Purged existing package."
fi

# 2. Locate newest package
LATEST_DEB=$(ls -t dist/${PACKAGE_NAME}*.deb 2>/dev/null | head -n 1)
if [ -z "$LATEST_DEB" ]; then
    print_error "No .deb package found in dist/. Please run build script first."
    exit 1
fi
print_status "Testing artifact: $LATEST_DEB"

# 3. Install
print_info "Installing $LATEST_DEB..."
sudo dpkg -i "$LATEST_DEB" || sudo apt-get install -f -y
print_status "Package installed."

# 4. Run test suite
if [ -d "tests" ]; then
    print_info "Running automated pytest suite..."
    export QT_QPA_PLATFORM=offscreen
    VENV_PY=""
    for py in "$PROJECT_ROOT/.venv/bin/python3" "$PROJECT_ROOT/venv/bin/python3" "$(which python3)"; do
        if [ -x "$py" ]; then VENV_PY="$py"; break; fi
    done
    
    if [ -n "$VENV_PY" ]; then
        "$VENV_PY" -m pytest tests -q --timeout=180 --timeout-method=thread || {
            print_error "Unit tests failed!"
            exit 1
        }
        print_status "Unit tests passed."
    fi
fi

# 5. Launch and Smoke-test application
print_info "Executing installed binary smoke test..."
mkdir -p "$APP_LOG_DIR"
> "$STDERR_LOG"

# Override with SMOKE_TEST_COMMAND="..." (default: print the installed app's version)
${SMOKE_TEST_COMMAND:-$PACKAGE_NAME --version} &
APP_PID=$!

sleep 3
if ps -p $APP_PID > /dev/null 2>&1; then
    print_status "Application process is running (PID: $APP_PID)."
    kill $APP_PID 2>/dev/null || true
fi

# Check for crash signatures in stderr log
if [ -f "$STDERR_LOG" ] && grep -Ei "Traceback|Segmentation fault|Core dumped" "$STDERR_LOG"; then
    print_error "Crash signature detected in stderr log:"
    cat "$STDERR_LOG"
    exit 1
fi

print_status "Debian package verification successful!"
