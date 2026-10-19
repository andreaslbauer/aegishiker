#!/bin/bash
# ==============================================================================
# aegishiker - Debian Package Builder
# Usage: ./build-deb.sh [VERSION] [--lite|--full]
# ==============================================================================

set -e

# Project Root Detection
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Terminal Formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_info() { echo -e "${YELLOW}[i]${NC} $1"; }

# Configuration
PACKAGE_NAME="aegishiker"
ARCH="${ARCH:-$(dpkg --print-architecture 2>/dev/null || echo amd64)}"
TEMPLATE_DIR="debian"           # version-controlled package files (DEBIAN/, usr/share/, templates/)
BUILD_DIR="build/deb-root"      # the package tree is assembled here, never inside debian/
DIST_DIR="dist"

# Version Resolution
VERSION=""
BUILD_MODE="default"

for arg in "$@"; do
    if [ "$arg" == "--lite" ]; then
        BUILD_MODE="lite"
    elif [ "$arg" == "--full" ]; then
        BUILD_MODE="full"
    elif [[ ! "$arg" == --* && -z "$VERSION" ]]; then
        VERSION="$arg"
    fi
done

if [ -z "$VERSION" ]; then
    if [ -f "version.txt" ]; then
        VERSION=$(cat version.txt | tr -d '[:space:]')
    else
        VERSION="1.0.0"
    fi
fi

PACKAGE_FILE="${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Building Debian Package: ${PACKAGE_NAME}${NC}"
echo -e "${BLUE}Version: ${VERSION} [Mode: ${BUILD_MODE}]${NC}"
echo -e "${BLUE}========================================${NC}"

# Prune virtualenv to minimize package size
prune_venv() {
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    local SITE_PACKAGES="venv/lib/python${PYTHON_VERSION}/site-packages"
    print_info "Pruning virtual environment (Python ${PYTHON_VERSION}) for smallest package size..."

    # 1. Remove unused GUI modules if Qt/PySide is used
    if [ -d "$SITE_PACKAGES/PySide6" ]; then
        find "$SITE_PACKAGES/PySide6" -maxdepth 1 -type f -name "*.abi3.so" \
            | grep -vE "QtCore|QtGui|QtWidgets|QtWebEngine|QtWebChannel|QtNetwork|QtDBus|QtPositioning|QtQml|QtQuick|QtPrintSupport|QtMultimedia" \
            | xargs rm -f 2>/dev/null || true
        rm -rf "$SITE_PACKAGES/PySide6/include"
        rm -rf "$SITE_PACKAGES/PySide6/doc"
    fi

    # 2. Delete static analysis stubs and tests
    find "$SITE_PACKAGES" -type f -name "*.pyi" -delete
    find "$SITE_PACKAGES" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
    
    # 3. Strip symbols from all shared objects
    if command -v strip &> /dev/null; then
        print_info "Stripping debug symbols from binaries..."
        find "$SITE_PACKAGES" -type f -name "*.so*" -exec strip --strip-unneeded {} + 2>/dev/null || true
    fi

    # 4. Clean bytecode caches
    find "$SITE_PACKAGES" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    print_status "Pruning complete"
}

# 1. Verify build dependencies
for cmd in python3 dpkg-deb; do
    if ! command -v $cmd &> /dev/null; then
        print_error "$cmd is required but not installed."
        exit 1
    fi
done

# 2. Clean workspace (and the staging left in debian/ by older versions of this script)
rm -rf "$BUILD_DIR" "$DIST_DIR" "$TEMPLATE_DIR/opt" "$TEMPLATE_DIR/usr/bin"
mkdir -p "$DIST_DIR" "$BUILD_DIR/opt/$PACKAGE_NAME"
cp -r "$TEMPLATE_DIR/DEBIAN" "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/usr"
cp -r "$TEMPLATE_DIR/usr/share" "$BUILD_DIR/usr/share"

# 3. Copy application files: code, bundled regional packs and reference data, default config
print_info "Copying application files..."
for item in main.py src data config version.txt requirements.txt LICENSES.html; do
    if [ ! -e "$item" ]; then
        print_error "Missing $item"
        exit 1
    fi
    cp -r "$item" "$BUILD_DIR/opt/$PACKAGE_NAME/"
done
find "$BUILD_DIR/opt/$PACKAGE_NAME" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 4. Configure launcher scripts and the menu icon
print_info "Configuring launcher and desktop integration..."
mkdir -p "$BUILD_DIR/usr/bin" "$BUILD_DIR/usr/share/icons/hicolor/scalable/apps"
cp "$TEMPLATE_DIR/templates/${PACKAGE_NAME}.launcher" "$BUILD_DIR/opt/$PACKAGE_NAME/${PACKAGE_NAME}"
chmod +x "$BUILD_DIR/opt/$PACKAGE_NAME/${PACKAGE_NAME}"
cp "$TEMPLATE_DIR/templates/${PACKAGE_NAME}.wrapper" "$BUILD_DIR/usr/bin/${PACKAGE_NAME}"
chmod +x "$BUILD_DIR/usr/bin/${PACKAGE_NAME}"
cp src/assets/appicon.svg "$BUILD_DIR/usr/share/icons/hicolor/scalable/apps/${PACKAGE_NAME}.svg"

# 5. Build isolated runtime virtualenv
print_info "Assembling isolated Python runtime..."
cd "$BUILD_DIR/opt/$PACKAGE_NAME"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi
prune_venv
deactivate
cd - > /dev/null

# 6. Set maintainer script permissions
chmod 755 "$BUILD_DIR/DEBIAN/postinst" "$BUILD_DIR/DEBIAN/prerm" 2>/dev/null || true

# 7. Update control metadata & compute installed size
sed -i "s/^Version:.*/Version: $VERSION/" "$BUILD_DIR/DEBIAN/control"
sed -i "s/^Package:.*/Package: ${PACKAGE_NAME}/" "$BUILD_DIR/DEBIAN/control"
sed -i "s/^Architecture:.*/Architecture: ${ARCH}/" "$BUILD_DIR/DEBIAN/control"
sed -i '/^Installed-Size:/d' "$BUILD_DIR/DEBIAN/control"
INSTALLED_SIZE=$(du -sk --exclude=DEBIAN "$BUILD_DIR" | cut -f1)
echo "Installed-Size: $INSTALLED_SIZE" >> "$BUILD_DIR/DEBIAN/control"

# 8. Build package
print_info "Building .deb package..."
dpkg-deb --root-owner-group --build "$BUILD_DIR" "$DIST_DIR/$PACKAGE_FILE"
print_status "Package built: $DIST_DIR/$PACKAGE_FILE"

# 9. Emit metadata manifest
PACKAGE_SIZE=$(stat -c%s "$DIST_DIR/$PACKAGE_FILE")
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$DIST_DIR/package.json" <<EOF
{
  "name": "${PACKAGE_NAME}",
  "version": "${VERSION}",
  "file": "${PACKAGE_FILE}",
  "build_time": "${BUILD_TIME}",
  "size": ${PACKAGE_SIZE}
}
EOF

print_status "Build completed successfully! Package size: $(du -h "$DIST_DIR/$PACKAGE_FILE" | cut -f1)"
