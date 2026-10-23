# Packaging AegisHiker (Ubuntu `.deb` & Snap)

AegisHiker uses size-optimized Debian (`.deb`) and Snap packaging scripts located in `tools/build/`, `debian/`, `packaging/`, and `snap/`.

## 1. Debian Package (`.deb`)

### Build
```bash
./tools/build/build_deb.sh
# or
./tools/build/build-deb.sh
```
This script:
- Reads the version from `version.txt`.
- Creates an isolated Python virtual environment in the staging tree (`/opt/aegishiker`).
- Prunes unused Qt/PySide6 bindings, C/C++ headers, `.pyi` stubs, and `__pycache__` folders, and strips shared libraries (`strip --strip-unneeded`).
- Installs the desktop entry (`packaging/aegishiker.desktop`) and SVG application icon (`src/assets/appicon.svg`).
- Outputs the `.deb` archive to `dist/`.

### Test & Verify
```bash
./tools/build/test-deb.sh
```
Installs the `.deb`, runs headless `pytest` (`QT_QPA_PLATFORM=offscreen`), performs a smoke test of the binary, and cleanly purges the test installation.

---

## 2. Snap Package (`.snap`)

### Build
```bash
./tools/build/build_snap.sh
# or
./tools/build/build-snap.sh
```
Uses `snap/snapcraft.yaml` (`base: core24`, `extensions: [gnome]`, `compression: xz`) to build a self-contained Snap with `network`, `network-bind` (for LAN mobile companion pairing), `home`, and `removable-media` plugs.

### Test & Verify
```bash
./tools/build/test-snap.sh
```

### Build, Test & Publish (candidate)
```bash
snapcraft login            # once
./tools/build/snap_package_test_deploy.sh
```
Builds, runs `test-snap.sh`, and only if tests pass uploads the snap and releases it to the `candidate` channel (`snapcraft upload --release=candidate`), then bumps the patch version in `version.txt`. Override the channel with `SNAP_RELEASE_CHANNEL=beta`.

