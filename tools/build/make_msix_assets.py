#!/usr/bin/env python3
"""Render src/assets/appicon.svg into the PNG logos an MSIX manifest needs, plus a .ico for the exe.

Usage: make_msix_assets.py <assets_out_dir> <ico_out_path>
Run by tools/build/build_msix.ps1 inside the build venv (PySide6 renders the SVG, Pillow writes the .ico).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent.parent
SVG = ROOT / "src" / "assets" / "appicon.svg"

# file name -> (width, height); the icon is centred on a transparent canvas for non-square logos
LOGOS = {
    "Square44x44Logo.png": (44, 44),
    "Square150x150Logo.png": (150, 150),
    "Wide310x150Logo.png": (310, 150),
    "StoreLogo.png": (50, 50),
}
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
_APP = None


def render(renderer: QSvgRenderer, width: int, height: int) -> QImage:
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    side = min(width, height)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF((width - side) / 2, (height - side) / 2, side, side))
    painter.end()
    return img


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    assets_dir, ico_path = Path(sys.argv[1]), Path(sys.argv[2])
    assets_dir.mkdir(parents=True, exist_ok=True)
    ico_path.parent.mkdir(parents=True, exist_ok=True)

    global _APP
    _APP = QGuiApplication([])  # QPainter needs a GUI application
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        print(f"Cannot render {SVG}", file=sys.stderr)
        return 1

    for name, (w, h) in LOGOS.items():
        if not render(renderer, w, h).save(str(assets_dir / name)):
            print(f"Failed to write {name}", file=sys.stderr)
            return 1

    big = assets_dir / "_icon256.png"
    render(renderer, 256, 256).save(str(big))
    with Image.open(big) as im:
        im.save(ico_path, sizes=[(s, s) for s in ICO_SIZES])
    big.unlink()
    print(f"Wrote {len(LOGOS)} logos to {assets_dir} and {ico_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
