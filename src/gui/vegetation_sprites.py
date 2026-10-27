"""Painted plant sprites for the 3D hike replay: trees, shrubs, grass tussocks and boulders.

Each species gets a few variants, painted once at 128 px tall with a soft ground shadow and lit
from the upper left (like the terrain), then scaled down into mip levels so small, distant plants
stay smooth. Deciduous foliage follows the season: summer greens, autumn reds, oranges and golds,
bare branches in winter.
"""
import math
import random
import zlib
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient, QLinearGradient

BASE_PX = 128
GROUND_FRAC = (BASE_PX - 5) / BASE_PX  # where the plant meets the ground, as a share of sprite height
VARIANTS = 4
SHADE_ALPHA = 0.42  # how dark plants in shadow (cloud or terrain) are drawn
ASPECT = {  # sprite width / height
    "broadleaf": 0.85, "aspen": 0.5, "spruce": 0.42, "fir": 0.62, "pine": 0.62, "lodgepole": 0.38,
    "juniper": 1.0, "joshua": 0.95, "krummholz": 2.4, "shrub": 1.5, "sage": 1.6, "grass": 1.8, "boulder": 1.5,
}

RGB = Tuple[int, int, int]


class Sprite:
    def __init__(self, image: QImage) -> None:
        self.aspect = image.width() / image.height()
        self.mips: List[QPixmap] = []  # BASE_PX tall, then halved down to 16 px
        img = image
        while True:
            self.mips.append(QPixmap.fromImage(img))
            if img.height() <= 16:
                break
            img = img.scaled(max(1, img.width() // 2), img.height() // 2,
                             Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)

        self.levels = [(pix, QRectF(pix.rect())) for pix in self.mips]  # with their source rects
        self._shaded: Optional[List[Tuple[QPixmap, QRectF]]] = None

    def shaded_levels(self) -> List[Tuple[QPixmap, QRectF]]:
        """The mip levels darkened as in shadow (made on first use)."""
        if self._shaded is None:
            self._shaded = []
            for pix, src in self.levels:
                dark = QPixmap(pix)
                p = QPainter(dark)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
                p.fillRect(dark.rect(), QColor(8, 16, 34, int(255 * SHADE_ALPHA)))
                p.end()
                self._shaded.append((dark, src))
        return self._shaded

    def level_for(self, height_px: float, shaded: bool = False) -> Tuple[QPixmap, QRectF]:
        """Smallest mip level at least `height_px` tall (so it is only ever scaled down)."""
        levels = self.shaded_levels() if shaded else self.levels
        if height_px <= 16 and len(levels) > 3:
            return levels[3]
        if height_px <= 32 and len(levels) > 2:
            return levels[2]
        if height_px <= 64 and len(levels) > 1:
            return levels[1]
        return levels[0]


def _c(rgb: RGB, k: float = 1.0, a: int = 255) -> QColor:
    return QColor(min(255, int(rgb[0] * k)), min(255, int(rgb[1] * k)), min(255, int(rgb[2] * k)), a)


def _jitter(rgb: RGB, rng: random.Random, amount: int = 10) -> RGB:
    return tuple(max(0, min(255, ch + rng.randint(-amount, amount))) for ch in rgb)  # type: ignore[return-value]


def _blob(p: QPainter, x: float, y: float, rx: float, ry: float, rgb: RGB) -> None:
    """Leafy clump lit from the upper left."""
    g = QRadialGradient(QPointF(x - rx * 0.35, y - ry * 0.4), max(rx, ry) * 1.25)
    g.setColorAt(0.0, _c(rgb, 1.35))
    g.setColorAt(0.55, _c(rgb))
    g.setColorAt(1.0, _c(rgb, 0.62))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QPointF(x, y), rx, ry)


def _trunk(p: QPainter, x: float, y0: float, y1: float, width: float, rgb: RGB) -> None:
    g = QLinearGradient(x - width / 2, 0, x + width / 2, 0)
    g.setColorAt(0.0, _c(rgb, 1.25))
    g.setColorAt(1.0, _c(rgb, 0.6))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawPolygon(QPolygonF([QPointF(x - width / 2, y0), QPointF(x - width * 0.3, y1),
                             QPointF(x + width * 0.3, y1), QPointF(x + width / 2, y0)]))


def _bare_branches(p: QPainter, x: float, y: float, length: float, angle: float, width: float,
                   depth: int, rgb: RGB, rng: random.Random) -> None:
    x2, y2 = x + math.cos(angle) * length, y - math.sin(angle) * length
    p.setPen(QPen(_c(rgb, 1.0, 230), max(0.8, width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(x, y), QPointF(x2, y2))
    if depth > 0:
        for da in (-0.45, 0.4) + ((0.0,) if depth > 2 else ()):
            _bare_branches(p, x2, y2, length * rng.uniform(0.6, 0.78), angle + da + rng.uniform(-0.15, 0.15),
                           width * 0.62, depth - 1, rgb, rng)


def _conifer(p: QPainter, w: float, h: float, gy: float, rgb: RGB, tiers: int, spread: float, rng: random.Random) -> None:
    """Tiered spire (spruce, fir, hemlock): jagged, drooping branch tiers, narrowing to a point."""
    cx = w / 2
    _trunk(p, cx, gy, gy - h * 0.16, w * 0.09, (86, 62, 44))
    top, bottom = 3.0, gy - h * 0.07
    left: List[QPointF] = [QPointF(cx, top)]
    right: List[QPointF] = [QPointF(cx, top)]
    for k in range(tiers):
        y_in = top + (bottom - top) * (k + 0.55) / tiers
        y_out = top + (bottom - top) * (k + 1) / tiers
        hw = w * spread * ((k + 1) / tiers) ** 0.9 * rng.uniform(0.88, 1.08)
        left += [QPointF(cx - hw * 0.5, y_in), QPointF(cx - hw, y_out)]
        right += [QPointF(cx + hw * 0.5, y_in + rng.uniform(-1, 1)), QPointF(cx + hw * rng.uniform(0.9, 1.05), y_out)]
    g = QLinearGradient(cx - w * spread, 0, cx + w * spread, 0)
    g.setColorAt(0.0, _c(rgb, 1.3))
    g.setColorAt(0.5, _c(rgb))
    g.setColorAt(1.0, _c(rgb, 0.62))
    p.setPen(QPen(_c(rgb, 0.55), 1.0))
    p.setBrush(QBrush(g))
    p.drawPolygon(QPolygonF(left + list(reversed(right))))


def _paint(species: str, season: str, rock: RGB, variant: int) -> QImage:
    rng = random.Random(zlib.crc32(f"{species}/{variant}".encode()))
    h = float(BASE_PX)
    w = max(8.0, round(h * ASPECT[species]))
    img = QImage(int(w), int(h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    gy = h * GROUND_FRAC
    cx = w / 2
    # Soft contact shadow
    shadow = QRadialGradient(QPointF(cx, gy), w * 0.42)
    shadow.setColorAt(0.0, QColor(0, 0, 0, 70))
    shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(shadow))
    p.drawEllipse(QPointF(cx, gy), w * 0.42, 5.0)

    if species == "spruce":
        _conifer(p, w, h, gy, _jitter((32, 70, 46), rng, 6), 8, 0.47, rng)
    elif species == "fir":
        _conifer(p, w, h, gy, _jitter((42, 84, 52), rng, 6), 7, 0.49, rng)
    elif species in ("pine", "lodgepole"):
        tall = species == "lodgepole"
        bark = (140, 92, 62) if not tall else (112, 88, 66)
        crown_top = h * (0.05 if not tall else 0.03)
        crown_bottom = h * (0.5 if not tall else 0.58)
        _trunk(p, cx, gy, crown_top + h * 0.08, w * (0.08 if not tall else 0.1), bark)
        rgb = _jitter((56, 92, 52) if not tall else (50, 86, 50), rng, 6)
        n = 8 if not tall else 9
        for k in range(n):
            t = k / (n - 1)
            y = crown_top + (crown_bottom - crown_top) * t + rng.uniform(-3, 3)
            spread = (0.3 + 0.12 * math.sin(t * math.pi)) if not tall else 0.2
            x = cx + (w * spread * rng.uniform(0.2, 1.0)) * (1 if k % 2 else -1)
            p.setPen(QPen(_c(bark, 0.8), 1.4))
            p.drawLine(QPointF(cx, y + 4), QPointF(x, y))
            _blob(p, x, y, w * (0.2 if not tall else 0.17) * rng.uniform(0.8, 1.15), h * 0.06 * rng.uniform(0.8, 1.2), rgb)
    elif species in ("broadleaf", "aspen"):
        aspen = species == "aspen"
        bark = (222, 222, 210) if aspen else (92, 74, 58)
        if season == "winter":
            _trunk(p, cx, gy, h * 0.45, w * (0.07 if aspen else 0.1), bark)
            twig = (196, 196, 186) if aspen else (96, 84, 72)
            for a in (math.pi / 2 + 0.5, math.pi / 2 - 0.5, math.pi / 2 + 0.1):
                _bare_branches(p, cx, h * 0.48, h * (0.2 if aspen else 0.24), a + rng.uniform(-0.1, 0.1),
                               w * (0.035 if aspen else 0.05), 4, twig, rng)
        else:
            _trunk(p, cx, gy, h * 0.42, w * (0.07 if aspen else 0.1), bark)
            if aspen:
                for k in range(4):  # dark knots on white bark
                    p.setBrush(QColor(50, 46, 40))
                    p.drawEllipse(QPointF(cx + rng.uniform(-1.5, 1.5), gy - h * (0.12 + 0.08 * k)), 1.6, 1.0)
            if season == "autumn":
                palette = [(236, 192, 46), (226, 178, 40), (240, 204, 70), (190, 176, 60)] if aspen else \
                          [(180, 60, 40), (206, 122, 42), (214, 176, 62), (150, 114, 54)]
                rgb = palette[variant % len(palette)]
            else:
                rgb = (112, 160, 72) if aspen else [(70, 118, 52), (82, 126, 58), (62, 108, 50), (76, 114, 46)][variant]
            ccx, ccy = cx, h * (0.32 if aspen else 0.34)
            rx, ry = w * (0.26 if aspen else 0.3), h * (0.26 if aspen else 0.22)
            for _k in range(8):
                a = rng.uniform(0, math.tau)
                d = rng.uniform(0.0, 0.85)
                _blob(p, ccx + math.cos(a) * rx * d, ccy + math.sin(a) * ry * d,
                      w * rng.uniform(0.18, 0.25), h * rng.uniform(0.09, 0.13), _jitter(rgb, rng, 12))
    elif species == "juniper":
        rgb = (78, 104, 80) if variant % 2 else (66, 96, 58)  # juniper blue-green / pinyon green
        p.setPen(QPen(_c((104, 90, 74)), w * 0.06, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, gy), QPointF(cx + rng.uniform(-6, 6), gy - h * 0.3))
        for _k in range(7):
            _blob(p, cx + rng.uniform(-0.3, 0.3) * w, h * rng.uniform(0.3, 0.68),
                  w * rng.uniform(0.17, 0.26), h * rng.uniform(0.13, 0.19), _jitter(rgb, rng, 8))
    elif species == "joshua":
        bark = (122, 104, 84)
        p.setPen(QPen(_c(bark), w * 0.09, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        fork = QPointF(cx, h * 0.52)
        p.drawLine(QPointF(cx, gy), fork)
        ends = []
        for k in range(rng.randint(2, 4)):
            a = math.pi / 2 + rng.uniform(-0.9, 0.9)
            mid = QPointF(fork.x() + math.cos(a) * w * 0.2, fork.y() - math.sin(a) * h * 0.14)
            end = QPointF(mid.x() + math.cos(a * 0.8 + 0.3) * w * 0.12, mid.y() - h * rng.uniform(0.08, 0.16))
            p.setPen(QPen(_c(bark, 0.95), w * 0.065, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(fork, mid)
            p.drawLine(mid, end)
            ends.append(end)
        for e in ends:  # spiky leaf rosettes
            p.setPen(QPen(_c((98, 118, 58)), 1.6))
            for k in range(14):
                a = k / 14 * math.tau
                p.drawLine(e, QPointF(e.x() + math.cos(a) * w * 0.1, e.y() + math.sin(a) * h * 0.06))
            _blob(p, e.x(), e.y(), w * 0.05, h * 0.03, (110, 128, 64))
    elif species == "krummholz":
        rgb = _jitter((40, 74, 48), rng, 6)
        for _k in range(14):
            x = rng.uniform(0.1, 0.9) * w
            top = 0.5 + 0.45 * (1 - abs(x / w - 0.45) * 2)  # higher in the middle, flagged downwind
            _blob(p, x, gy - h * rng.uniform(0.15, top * 0.6), w * rng.uniform(0.07, 0.12), h * rng.uniform(0.14, 0.24), rgb)
    elif species in ("shrub", "sage"):
        if species == "sage":
            rgb = (138, 148, 118)
        elif season == "autumn":
            rgb = [(150, 62, 44), (168, 98, 48), (134, 70, 48), (150, 116, 56)][variant]  # heath and blueberry
        elif season == "winter":
            rgb = (112, 98, 82)
        else:
            rgb = (72, 112, 56)
        for _k in range(6 if species == "shrub" else 8):
            _blob(p, cx + rng.uniform(-0.3, 0.3) * w, gy - h * rng.uniform(0.25, 0.55),
                  w * rng.uniform(0.16, 0.24), h * rng.uniform(0.2, 0.32), _jitter(rgb, rng, 10))
    elif species == "grass":
        rgb = {"autumn": (186, 162, 94), "winter": (170, 150, 112)}.get(season, (128, 148, 74))
        for _k in range(26):
            a = math.pi / 2 + rng.uniform(-1.05, 1.05)
            length = h * rng.uniform(0.55, 0.95)
            tip = QPointF(cx + math.cos(a) * length * 0.9, gy - math.sin(a) * length)
            col = _jitter(rgb, rng, 18)
            p.setPen(QPen(_c(col, rng.uniform(0.75, 1.2)), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx + rng.uniform(-w * 0.1, w * 0.1), gy), tip)
    elif species == "boulder":
        rgb = _jitter(rock, rng, 8)
        pts = []
        n = 11
        for k in range(n):
            a = math.pi + k / (n - 1) * math.pi  # upper half, left to right
            pts.append(QPointF(cx + math.cos(a) * w * 0.44 * rng.uniform(0.85, 1.0),
                               gy + math.sin(a) * h * 0.8 * rng.uniform(0.8, 1.0)))
        g = QLinearGradient(cx - w * 0.3, gy - h * 0.7, cx + w * 0.3, gy)
        g.setColorAt(0.0, _c(rgb, 1.3))
        g.setColorAt(1.0, _c(rgb, 0.6))
        p.setPen(QPen(_c(rgb, 0.5), 1.0))
        p.setBrush(QBrush(g))
        p.drawPolygon(QPolygonF(pts))
    p.end()
    return img


@lru_cache(maxsize=8)
def make_sprites(season: str, rock: RGB) -> Dict[str, List[Sprite]]:
    """species -> sprite variants for this season (rock colour tints the boulders)."""
    return {sp: [Sprite(_paint(sp, season, rock, v)) for v in range(VARIANTS)] for sp in ASPECT}
