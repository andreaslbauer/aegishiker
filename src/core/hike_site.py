"""Export a recorded hike as a self-contained website folder.

The site has a summary page (when, where, who, weather, numbers, trails taken), an interactive
topographic map with the elevation profile and photo pins, a photo gallery with each photo's place
on the trail, and "Play the Hike": the app's 3D replay, rendered in the browser.

Everything is static (HTML, CSS, JavaScript, JPEG) and works opened from the folder or from any
web server. Data is loaded with <script> tags, not fetch(), so file:// works too.

With a password, the hike data and every image are encrypted (AES-256-GCM, key from the password
with PBKDF2-SHA256) and decrypted in the browser after the password is entered; the files on the
server reveal nothing without it. Browsers only offer this cryptography on https:// sites and
local files, so a protected site must be served over https.
"""
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.hiking.geo import haversine_m, simplify_polyline_dp
from src.hiking.hike_info import shows_people
from src.hiking.hike_report import build_hike_summary, leg_at, trail_legs
from src.hiking.models import ComposedRoute, DestinationPack, HikePhotoPin, HikeSession
from src.hiking.photo_matcher import dedupe_pins
from src.hiking.replay_plan import plan_photo_stops
from src.hiking.terrain3d import LAYER_STYLES, LocalFrame, build_mesh, replay_map_layers, route_path, shade_mesh
from src.hiking.vegetation import TRAIL_CLEAR_M, build_vegetation

from . import paths

Progress = Optional[Callable[[str], None]]

SITE_MARKER = ".aegishiker-site"  # marks a folder this exporter owns (safe to refresh)
TEMPLATE_DIR = paths.WEB_DIR / "hikesite"
PBKDF2_ITERATIONS = 310_000
MAP_MARGIN_M = 700.0  # map area around the hike and its photos
RELIEF_UPSCALE = 4
REPLAY_CELL_M = 120.0
REPLAY_PAD_M = 5000.0  # terrain drawn around the route (the 3D view sees ~4.2 km)
Z_EXAGGERATION = 1.5
VEG_GRID_BASE = 48  # habitat index i is sent as chr(48 + i)
RAW_EXTS = {".rw2", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".raf", ".pef", ".srw"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mts", ".3gp"}

LINE_KINDS = ("path", "track", "road_minor", "road_major", "stream", "river", "cliff", "aerialway")
AREA_KINDS = ("forest", "open", "wetland", "bare_rock", "scree", "water", "reserve", "glacier")
POINT_KINDS = ("peak", "saddle", "parking", "trailhead", "viewpoint", "waterfall", "shelter", "hut",
               "campsite", "spring", "drinking_water", "ranger_station", "toilets", "picnic")


@dataclass
class SiteOptions:
    password: str = ""  # empty: an open site
    units: str = "us"  # "us" (mi / ft / °F) or "metric"; visitors can switch
    photo_px: int = 1800  # longest side of gallery photos
    skip_people: bool = False  # leave out photos with people in them
    thumb_px: int = 480
    jpeg_quality: int = 84


class SiteExportError(RuntimeError):
    pass


# ── photos ─────────────────────────────────────────────────────────────────────

_heif_ready: Optional[bool] = None


def _register_heif() -> None:
    global _heif_ready
    if _heif_ready is None:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            _heif_ready = True
        except Exception:
            _heif_ready = False


def _photo_sources(pin: HikePhotoPin) -> List[str]:
    """Readable files for a pin: the original, a JPEG/HEIC next to a RAW file, a converted copy,
    then AegisPhotoVoyager's 300 px thumbnail as a last resort."""
    out: List[str] = []
    if pin.photo_path:
        p = Path(pin.photo_path)
        ext = p.suffix.lower()
        if ext not in RAW_EXTS and ext not in VIDEO_EXTS:
            out.append(pin.photo_path)
        if ext in RAW_EXTS:
            out += [str(p.with_suffix(s)) for s in (".jpg", ".JPG", ".jpeg", ".JPEG", ".heic", ".HEIC")]
        out.append(pin.photo_path + ".jpg")
    if pin.thumb_path:
        out.append(pin.thumb_path)
    return out


def _open_photo(pin: HikePhotoPin):
    """A Pillow RGB image of the photo upright (EXIF orientation applied), or None."""
    from PIL import Image, ImageOps

    _register_heif()
    for src in _photo_sources(pin):
        if not Path(src).is_file():
            continue
        try:
            with Image.open(src) as im:
                im.load()
                return ImageOps.exif_transpose(im).convert("RGB")
        except Exception:
            continue
    return None


def _jpeg(img, max_side: int, quality: int) -> Tuple[bytes, int, int]:
    """Re-encoded copy without EXIF (no GPS / camera serials in the published files)."""
    from PIL import Image

    im = img.copy()
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue(), im.width, im.height


# ── basemap ────────────────────────────────────────────────────────────────────


def _bbox(points: Sequence[Tuple[float, float]], margin_m: float) -> Tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) around (lon, lat) points, grown by margin_m."""
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    lat0 = (min(lats) + max(lats)) / 2
    dlat = margin_m / 110_540.0
    dlon = margin_m / (111_320.0 * math.cos(math.radians(lat0)))
    return min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat


def _inside(c: Sequence[float], bb: Tuple[float, float, float, float]) -> bool:
    return bb[0] <= c[0] <= bb[2] and bb[1] <= c[1] <= bb[3]


def _feature_bbox_hits(coords: Sequence[Sequence[float]], bb) -> bool:
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return not (max(lons) < bb[0] or min(lons) > bb[2] or max(lats) < bb[1] or min(lats) > bb[3])


def _clip_runs(coords: Sequence[Sequence[float]], bb) -> List[List[List[float]]]:
    """Pieces of a polyline inside the box (keeping one vertex beyond each edge so lines run off
    the map instead of stopping short of it)."""
    runs: List[List[List[float]]] = []
    cur: List[List[float]] = []
    prev = None
    for c in coords:
        inside = _inside(c, bb)
        if inside:
            if not cur and prev is not None:
                cur.append(list(prev))
            cur.append(list(c))
        elif cur:
            cur.append(list(c))
            runs.append(cur)
            cur = []
        prev = c
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def _simplify(coords: Sequence[Sequence[float]], tol_m: float) -> List[List[float]]:
    pts = simplify_polyline_dp([[c[0], c[1], 0.0] for c in coords], tol_m)
    return [[round(p[0], 6), round(p[1], 6)] for p in pts]


def _relief_png(pack: DestinationPack, bb) -> Optional[Tuple[bytes, List[float]]]:
    """Shaded relief of the map area from the region's elevation model, light daylight palette.
    Returns (PNG bytes, [min_lon, min_lat, max_lon, max_lat] the image covers)."""
    dem = pack.dem
    if dem is None or dem.rows < 3 or dem.cols < 3:
        return None
    from PIL import Image

    dlon = (dem.max_lon - dem.min_lon) / (dem.cols - 1)
    dlat = (dem.max_lat - dem.min_lat) / (dem.rows - 1)
    c0 = max(0, int((bb[0] - dem.min_lon) / dlon) - 1)
    c1 = min(dem.cols - 1, int(math.ceil((bb[2] - dem.min_lon) / dlon)) + 1)
    r0 = max(0, int((bb[1] - dem.min_lat) / dlat) - 1)
    r1 = min(dem.rows - 1, int(math.ceil((bb[3] - dem.min_lat) / dlat)) + 1)
    if c1 - c0 < 2 or r1 - r0 < 2:
        return None
    g = dem.elevations_m
    lo = min(min(g[r][c0:c1 + 1]) for r in range(r0, r1 + 1))
    hi = max(max(g[r][c0:c1 + 1]) for r in range(r0, r1 + 1))
    span = max(1.0, hi - lo)
    lat_mid = dem.min_lat + (r0 + r1) / 2 * dlat
    dx = dlon * 111_320.0 * math.cos(math.radians(lat_mid))
    dy = dlat * 110_540.0
    az, alt = math.radians(315.0), math.radians(45.0)
    lx, ly, lz = math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt)
    zf = 2.0 if span < 400 else 1.4
    stops = [(0.0, (214, 226, 196)), (0.4, (196, 214, 170)), (0.75, (214, 204, 172)), (1.0, (236, 230, 220))]

    def tint(t: float):
        for (t0, a), (t1, b) in zip(stops, stops[1:]):
            if t <= t1:
                f = (t - t0) / (t1 - t0)
                return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))
        return stops[-1][1]

    w, h = c1 - c0 + 1, r1 - r0 + 1
    img = Image.new("RGB", (w, h))
    px = img.load()
    for r in range(r0, r1 + 1):
        up, dn = g[min(dem.rows - 1, r + 1)], g[max(0, r - 1)]
        row = g[r]
        for c in range(c0, c1 + 1):
            dzdx = (row[min(dem.cols - 1, c + 1)] - row[max(0, c - 1)]) * zf / (2 * dx)
            dzdy = (up[c] - dn[c]) * zf / (2 * dy)
            shade = (-dzdx * lx - dzdy * ly + lz) / math.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
            f = 0.62 + 0.48 * max(0.0, shade)
            base = tint((row[c] - lo) / span)
            px[c - c0, r1 - r] = tuple(min(255, int(ch * f)) for ch in base)  # image row 0 = north
    img = img.resize((w * RELIEF_UPSCALE, h * RELIEF_UPSCALE), Image.Resampling.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    # Pixels are centred on grid vertices: the image spans half a cell beyond the outer vertices
    covers = [dem.min_lon + (c0 - 0.5) * dlon, dem.min_lat + (r0 - 0.5) * dlat,
              dem.min_lon + (c1 + 0.5) * dlon, dem.min_lat + (r1 + 0.5) * dlat]
    return buf.getvalue(), [round(v, 7) for v in covers]


def build_basemap(pack: Optional[DestinationPack], bb) -> Dict:
    """Map data for the hike area: contours, landcover, water, paths, roads and named places."""
    out: Dict = {"bbox": [round(v, 6) for v in bb], "contours": [], "lines": [], "areas": [], "points": [],
                 "contour_interval_m": 0.0}
    if pack is None:
        return out
    interval = pack.contour_interval_m
    out["contour_interval_m"] = interval
    for line in pack.contours:
        if len(line.coords) < 2 or not _feature_bbox_hits(line.coords, bb):
            continue
        is_index = interval > 0 and abs(round(line.ele_m / (interval * 5)) * interval * 5 - line.ele_m) < 0.5
        for run in _clip_runs(line.coords, bb):
            out["contours"].append({"e": round(line.ele_m, 1), "i": 1 if is_index else 0, "c": _simplify(run, 4.0)})
    for f in pack.features:
        if not f.coords:
            continue
        if f.geom == "point":
            if f.kind in POINT_KINDS and _inside(f.coords[0], bb):
                out["points"].append({"k": f.kind, "n": f.name, "c": [round(f.coords[0][0], 6), round(f.coords[0][1], 6)],
                                      "e": f.ele_m})
        elif f.geom == "area" and f.kind in AREA_KINDS:
            if _feature_bbox_hits(f.coords, bb):
                ring = _simplify(f.coords, 6.0)
                if len(ring) >= 4:
                    out["areas"].append({"k": f.kind, "n": f.name, "c": ring})
        elif f.kind in LINE_KINDS or (f.kind == "water" and f.geom == "line"):
            if _feature_bbox_hits(f.coords, bb):
                kind = "path_hard" if f.kind == "path" and any(s in f.info for s in ("SAC T3", "SAC T4", "SAC T5", "SAC T6")) else f.kind
                for run in _clip_runs(f.coords, bb):
                    out["lines"].append({"k": kind, "n": f.name, "c": _simplify(run, 2.5)})
    for p in pack.pois:
        if _inside((p.lon, p.lat), bb) and not any(q["n"] == p.name for q in out["points"]):
            out["points"].append({"k": str(p.kind), "n": p.name, "c": [round(p.lon, 6), round(p.lat, 6)], "e": p.ele_m or None})
    return out


# ── 3D replay ──────────────────────────────────────────────────────────────────


def build_replay(route: ComposedRoute, pack: Optional[DestinationPack],
                 pins: Sequence[HikePhotoPin], month: Optional[int] = None) -> Dict:
    """Terrain mesh, route path, photo stops, peak labels and vegetation for the browser 3D replay
    (the same scene as the desktop replay in src/gui/replay3d.py)."""
    coords = route.coords_3d
    lats = [c[1] for c in coords]
    lons = [c[0] for c in coords]
    frame = LocalFrame(sum(lats) / len(lats), sum(lons) / len(lons), Z_EXAGGERATION)
    path = route_path(coords, frame)
    xs = [p[0] for p in path.pts]
    ys = [p[1] for p in path.pts]
    mesh = build_mesh(
        pack.dem if pack else None, frame,
        (min(xs) - REPLAY_PAD_M, min(ys) - REPLAY_PAD_M, max(xs) + REPLAY_PAD_M, max(ys) + REPLAY_PAD_M),
        cell_m=REPLAY_CELL_M, fallback_ele=min(path.ele) if path.ele else 0.0,
    )
    # Life zones and land cover colour the terrain; the browser grows the plants from the habitat grid.
    # The hike's month (else its first photo's, as in the app) sets the foliage
    if month is None:
        taken = [p.date_taken for p in pins if p.date_taken]
        month = datetime.fromtimestamp(min(taken), tz=timezone.utc).month if taken else None
    veg = build_vegetation(pack, mesh, path.pts, month)
    shade_mesh(mesh, veg.ground_rgb)
    habitats, grid = veg.habitat_grid()
    index_of = {id(pin): i for i, pin in enumerate(pins)}
    stops = []
    for plan in plan_photo_stops(list(pins)):
        shown = [index_of[id(p)] for p in plan.shown if id(p) in index_of]
        if not shown:
            continue
        first = pins[shown[0]]
        x, y = frame.to_xy(first.lat, first.lon)
        stops.append({"photos": shown, "hidden": plan.hidden, "along": round(plan.along_m, 1),
                      "world": [round(x, 1), round(y, 1), round(mesh.height_at(x, y) + 6.0, 1)]})
    peaks = []
    if pack:
        x_lo, y_lo = mesh.x0, mesh.y0
        x_hi, y_hi = mesh.x0 + (mesh.cols - 1) * mesh.cell_m, mesh.y0 + (mesh.rows - 1) * mesh.cell_m
        for f in pack.features:
            if f.kind == "peak" and f.name and f.geom == "point":
                x, y = frame.to_xy(f.coords[0][1], f.coords[0][0])
                if x_lo <= x <= x_hi and y_lo <= y <= y_hi:
                    z = mesh.height_at(x, y)
                    ele = f.ele_m if f.ele_m is not None else z / Z_EXAGGERATION
                    peaks.append([f.name, round(x, 1), round(y, 1), round(z, 1), round(ele, 1)])
    layers = replay_map_layers(pack, mesh, route.trail_ids)
    codes = {k: i for i, k in enumerate(LAYER_STYLES)}
    return {
        "zx": Z_EXAGGERATION,
        # Other trails, paths, streams, rivers (segments draped on the terrain) and lakes (pieces per cell)
        "layers": {
            "names": list(LAYER_STYLES),
            "kinds": [codes[k] for k in layers.kinds],
            "segs": [round(v, 1) for seg in layers.segs for v in seg],
            "water": [{"cell": [r, c], "ring": [round(v, 1) for pt in ring for v in pt]}
                      for (r, c), rings in layers.water.items() for ring in rings],
        },
        "mesh": {
            "x0": round(mesh.x0, 2), "y0": round(mesh.y0, 2), "cell": mesh.cell_m,
            "cols": mesh.cols, "rows": mesh.rows,
            "z": [round(v, 1) for row in mesh.z for v in row],
            "color": [ch for row in mesh.color for col in row for ch in col],
        },
        "path": {
            "xyz": [round(v, 1) for p in path.pts for v in p],
            "ele": [round(e, 1) for e in path.ele],
        },
        "stops": stops,
        "peaks": peaks,
        "veg": {
            "season": veg.season, "rock": list(veg.biome.rock), "treeline": veg.treeline,
            "sub": round(mesh.cell_m / veg.cover.step), "clear": TRAIL_CLEAR_M,
            "habitats": [[h.name, h.density, [[sp, w] for sp, w in h.flora]] for h in habitats],
            "grid": "".join(chr(VEG_GRID_BASE + i) for i in grid),  # one character per land-cover sample
        },
    }


# ── encryption ─────────────────────────────────────────────────────────────────


class _Sealer:
    """AES-256-GCM with a key from the password (PBKDF2-HMAC-SHA256), matching the site's WebCrypto."""

    def __init__(self, password: str) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as e:  # optional dependency
            raise SiteExportError("Password protection needs the 'cryptography' package (pip install cryptography).") from e
        self.salt = os.urandom(16)
        self.key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), self.salt, PBKDF2_ITERATIONS, dklen=32)
        self._aes = AESGCM(self.key)

    def seal(self, data: bytes) -> Dict[str, str]:
        iv = os.urandom(12)
        ct = self._aes.encrypt(iv, data, None)  # ciphertext || 16-byte tag, as WebCrypto expects
        return {"iv": base64.b64encode(iv).decode("ascii"), "data": base64.b64encode(ct).decode("ascii")}


# ── export ─────────────────────────────────────────────────────────────────────


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "hike"


def default_site_dir(title: str, date_iso: str = "") -> Path:
    base = Path.home() / "AegisHiker Sites"
    return base / slugify(f"{date_iso} {title}".strip())


def prepare_folder(folder: Path) -> None:
    """Make `folder` ready for a (re-)export: create it, or clear the files a previous export
    wrote. Refuses to write into a non-empty folder that isn't one of ours."""
    folder = Path(folder)
    if folder.exists() and not folder.is_dir():
        raise SiteExportError(f"{folder} is a file, not a folder.")
    if folder.exists() and any(folder.iterdir()) and not (folder / SITE_MARKER).exists():
        raise SiteExportError(
            f"{folder} already contains other files. Choose an empty folder (or one a previous hike "
            "website was exported to)."
        )
    folder.mkdir(parents=True, exist_ok=True)
    for sub in ("photos", "thumbs", "assets"):
        shutil.rmtree(folder / sub, ignore_errors=True)
    for name in ("index.html", "hike.js", "site.js", "site.css", "relief.png", "relief.png.js"):
        (folder / name).unlink(missing_ok=True)
    (folder / SITE_MARKER).write_text("Exported by AegisHiker. Re-exporting replaces this folder's site files.\n",
                                      encoding="utf-8")


def _js_assign(name: str, payload) -> str:
    return f"window.{name} = {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))};\n"


def _photo_entry(i: int, pin: HikePhotoPin, legs, route_len: float) -> Dict:
    when = datetime.fromtimestamp(int(pin.date_taken)) if pin.date_taken else None
    leg = leg_at(legs, pin.distance_along_m)
    return {
        "id": f"p{i + 1:03d}",
        "caption": pin.caption if pin.caption and pin.caption != pin.filename else "",
        "filename": pin.filename,
        "ai": pin.ai_summary,
        "tags": list(pin.ai_tags),
        "mood": pin.ai_mood,
        "people": list(pin.people),
        "starred": pin.is_starred,
        "camera": pin.camera_model,
        "time_iso": when.isoformat(timespec="minutes") if when else "",
        "lat": round(pin.lat, 6),
        "lon": round(pin.lon, 6),
        "ele_m": round(pin.ele_m, 1),
        "along_m": round(max(0.0, min(route_len, pin.distance_along_m)), 1),
        "off_m": round(pin.cross_track_m, 1),
        "trail": leg.name if leg and leg.trail is not None else "",
    }


def export_hike_site(
    folder: Path,
    route: ComposedRoute,
    session: Optional[HikeSession],
    pack: Optional[DestinationPack],
    pins: Sequence[HikePhotoPin],
    options: Optional[SiteOptions] = None,
    progress: Progress = None,
    app_version: str = "",
) -> Dict:
    """Write the hike website into `folder`. Returns a report: files, photos, skipped photos."""
    opts = options or SiteOptions()
    say = progress or (lambda _m: None)
    if len(route.coords_3d) < 2:
        raise SiteExportError("This hike has no route to show.")
    folder = Path(folder)
    prepare_folder(folder)
    sealer = _Sealer(opts.password) if opts.password else None

    def write_asset(rel: str, data: bytes, mime: str) -> None:
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if sealer is None:
            target.write_bytes(data)
        else:
            blob = sealer.seal(data)
            blob["type"] = mime
            Path(str(target) + ".js").write_text(
                f"window.__hikeAsset({json.dumps(rel)},{json.dumps(blob)});\n", encoding="utf-8"
            )

    # Photos, in the order they were taken
    say("Collecting photos…")
    ordered = sorted(dedupe_pins(list(pins)), key=lambda p: (p.date_taken or 0, p.distance_along_m))
    people_left_out = 0
    if opts.skip_people:
        people_left_out = sum(1 for p in ordered if shows_people(p))
        ordered = [p for p in ordered if not shows_people(p)]
    legs = trail_legs(route, pack.trails if pack else [])
    photos: List[Dict] = []
    kept: List[HikePhotoPin] = []
    skipped: List[str] = []
    for n, pin in enumerate(ordered, 1):
        say(f"Preparing photo {n} of {len(ordered)}…")
        img = _open_photo(pin)
        if img is None:
            skipped.append(pin.filename or pin.photo_path)
            continue
        entry = _photo_entry(len(photos), pin, legs, route.distance_m)
        full, w, h = _jpeg(img, opts.photo_px, opts.jpeg_quality)
        thumb, tw, th = _jpeg(img, opts.thumb_px, 80)
        entry.update({"src": f"photos/{entry['id']}.jpg", "thumb": f"thumbs/{entry['id']}.jpg",
                      "w": w, "h": h, "tw": tw, "th": th})
        write_asset(entry["src"], full, "image/jpeg")
        write_asset(entry["thumb"], thumb, "image/jpeg")
        photos.append(entry)
        kept.append(pin)

    say("Building the map…")
    summary = build_hike_summary(session, route, pack, kept)
    pts = [(c[0], c[1]) for c in route.coords_3d] + [(p.lon, p.lat) for p in kept]
    bb = _bbox(pts, MAP_MARGIN_M)
    basemap = build_basemap(pack, bb)
    relief = _relief_png(pack, bb) if pack else None
    if relief:
        write_asset("relief.png", relief[0], "image/png")
        basemap["relief"] = {"src": "relief.png", "bbox": relief[1]}

    say("Building the 3D replay terrain…")
    date = summary.get("date_iso") or ""
    replay = build_replay(route, pack, kept, month=int(date[5:7]) if date[5:7].isdigit() else None)

    cum = [0.0]
    for a, b in zip(route.coords_3d, route.coords_3d[1:]):
        cum.append(cum[-1] + haversine_m(a[1], a[0], b[1], b[0]))
    data = {
        "meta": {
            "generator": f"AegisHiker {app_version}".strip(),
            "generated": datetime.now().isoformat(timespec="minutes"),
            "units": "metric" if opts.units == "metric" else "us",
            "protected": sealer is not None,
        },
        "summary": summary,
        "route": [[round(c[0], 6), round(c[1], 6), round(c[2] if len(c) > 2 else 0.0, 1)] for c in route.coords_3d],
        "route_along": [round(d, 1) for d in cum],
        "photos": photos,
        "basemap": basemap,
        "replay": replay,
    }

    say("Writing the website…")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if sealer is None:
        (folder / "hike.js").write_text(f"window.HIKE_DATA = {payload};\n", encoding="utf-8")
    else:
        locked = sealer.seal(payload.encode("utf-8"))
        locked.update({"salt": base64.b64encode(sealer.salt).decode("ascii"), "iter": PBKDF2_ITERATIONS})
        (folder / "hike.js").write_text(_js_assign("HIKE_LOCKED", locked), encoding="utf-8")

    for name in ("site.css", "site.js"):
        shutil.copyfile(TEMPLATE_DIR / name, folder / name)
    page = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
    if sealer is None:
        title = summary["title"]
        desc = " · ".join(x for x in (summary["date_label"], summary["area"]) if x)
    else:  # nothing about the hike is readable without the password
        title, desc = "A private hike", "This hike is password protected."
    page = page.replace("{{TITLE}}", _html(title)).replace("{{DESCRIPTION}}", _html(desc))
    (folder / "index.html").write_text(page, encoding="utf-8")

    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.name != SITE_MARKER)
    say(f"Done: {len(photos)} photos, {sum(p.stat().st_size for p in files) / 1e6:.1f} MB")
    return {
        "folder": str(folder),
        "index": str(folder / "index.html"),
        "photos": len(photos),
        "skipped_photos": skipped,
        "people_photos_left_out": people_left_out,
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "protected": sealer is not None,
    }


def _html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
