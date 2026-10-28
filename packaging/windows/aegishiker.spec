# PyInstaller spec for the Windows (MSIX) build — run by tools/build/build_msix.ps1.
#
# One folder, two launchers sharing it:
#   AegisHiker.exe      windowed, the Start menu entry (no console window)
#   aegishiker-cli.exe  console, behind the `aegishiker` execution alias (--version, --test-mode)
# Bundled reference data lands under sys._MEIPASS, where src/core/paths.py looks for it.
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent
ICON = os.environ.get("AEGISHIKER_ICO") or None  # rendered from appicon.svg by the build script

datas = [
    (str(ROOT / "data"), "data"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "src" / "assets"), "src/assets"),
    (str(ROOT / "src" / "web"), "src/web"),
    (str(ROOT / "version.txt"), "."),
    (str(ROOT / "LICENSES.html"), "."),
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AegisHiker", console=False, icon=ICON, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="aegishiker-cli", console=True, icon=ICON, upx=False)

coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="AegisHiker")
