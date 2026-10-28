# Packaging AegisHiker (Ubuntu `.deb`, Snap & Windows MSIX)

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
Installs the `.deb`, runs headless `pytest` (`QT_QPA_PLATFORM=offscreen`), and performs a smoke test of the binary.

### Build, Test & Deploy
```bash
./tools/build/deb_package_test_deploy.sh
```
Builds, runs `test-deb.sh`, and only if tests pass deploys the package — by default it installs the tested `.deb` on this machine (`sudo dpkg -i`) and verifies the installed version — then bumps the patch version in `version.txt`. Set `DEPLOY_COMMAND="..."` to deploy somewhere else instead.

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


---

## 3. Windows Package (`.msix`)

Requires the Windows 10/11 SDK (for `makeappx.exe` / `signtool.exe`, found automatically) and Python 3 (`py` launcher). Run from PowerShell in the project root.

### Build
```powershell
powershell -ExecutionPolicy Bypass -File tools\build\build_msix.ps1 [-Clean] [-NoSign]
```
This script:
- Creates a clean build venv (`build\msix\venv`) from `requirements.txt` plus PyInstaller, so dev tools never ship.
- Renders the MSIX logos and exe icon from `src/assets/appicon.svg` (`tools/build/make_msix_assets.py`).
- Freezes the app with `packaging/windows/aegishiker.spec`: `AegisHiker.exe` (windowed, Start menu) and `aegishiker-cli.exe` (console, behind the `aegishiker` command-line alias), sharing one folder.
- Fills in `packaging/windows/AppxManifest.xml`, packs with `makeappx`, signs with `signtool`, and writes `dist\AegisHiker_<version>_x64.msix` plus the signer's public `.cer`.

Signing: by default a self-signed certificate `CN=AegisHiker Dev` is created once in `Cert:\CurrentUser\My` and reused. For a real certificate set `MSIX_CERT_PATH` (`.pfx`), `MSIX_CERT_PASSWORD`, `MSIX_PUBLISHER` (its exact subject) and optionally `MSIX_TIMESTAMP_URL`. For the Microsoft Store, use `-NoSign` with the Partner Center values in `MSIX_IDENTITY_NAME` / `MSIX_PUBLISHER` / `MSIX_PUBLISHER_DISPLAY_NAME`.

The windowed exe has no stderr, so it logs to `~/.aegishiker/logs/aegishiker.log` and `faulthandler.log`.

### Test & Verify
```powershell
powershell -ExecutionPolicy Bypass -File tools\build\test-msix.ps1 [-SkipUnitTests] [-SkipGuiLaunch]
```
Trusts the package's signing certificate in `Cert:\LocalMachine\TrustedPeople` (one UAC prompt, first time only), reinstalls the newest `dist\*.msix`, checks the installed version, runs headless `pytest`, then smoke-tests the installed package: `aegishiker --version`, `aegishiker --test-mode` (offscreen end-to-end self-test) and a real launch of the Start menu entry.

### Build, Test & Deploy
```powershell
powershell -ExecutionPolicy Bypass -File tools\build\msix_package_test_deploy.ps1
```
Builds, runs `test-msix.ps1`, and only if tests pass deploys the package — by default it installs the tested `.msix` on this machine and verifies the installed version — then bumps the patch version in `version.txt`. Pass `-DeployCommand "..."` (or set `DEPLOY_COMMAND`) to deploy somewhere else instead; it runs with `$env:MSIX_PATH` and `$env:MSIX_CER_PATH` set. `-NoVersionBump` skips the bump.

To install on another machine, trust the `.cer` first (admin PowerShell):
`Import-Certificate -FilePath AegisHiker_<version>.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople`, then double-click the `.msix` or run `Add-AppxPackage <file>.msix`.
