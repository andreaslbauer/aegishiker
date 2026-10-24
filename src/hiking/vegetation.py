"""Region-authentic land cover and vegetation for the 3D hike replay (Qt-free).

Each region gets a *biome*: life zones stacked by elevation (northern hardwoods → spruce–fir →
krummholz → alpine tundra in the White Mountains; ponderosa → lodgepole & aspen → spruce–fir →
tundra in the Rockies; desert scrub → pinyon–juniper → ponderosa on the Colorado Plateau, ...).
Zone edges drop with latitude, and are ragged rather than drawn with a ruler. Where the region's
OpenStreetMap land cover says otherwise (bare rock, scree, glaciers, wetlands, meadows, lakes)
that wins, and cliffs are rock. The 3D view tints the terrain with each spot's ground colour and
scatters the spot's plants near the camera; the hike's month picks the foliage (autumn colours,
bare hardwoods in winter).
"""
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]
Plant = Tuple[float, float, float, str, int, float]  # x, y, z (mesh units), species, sprite variant, height (m)

# Real plant heights (m); the 3D view exaggerates them so they read from the chase camera
SPECIES_HEIGHT_M: Dict[str, Tuple[float, float]] = {
    "broadleaf": (15.0, 24.0),  # maple, beech, birch, oak, tulip poplar
    "aspen": (9.0, 16.0),
    "spruce": (10.0, 20.0),  # spruce / subalpine fir: narrow spires
    "fir": (18.0, 32.0),  # hemlock, Douglas-fir, white pine, red fir: broad cones
    "pine": (16.0, 30.0),  # ponderosa, Jeffrey, pitch pine: open crowns on tall trunks
    "lodgepole": (15.0, 24.0),
    "juniper": (3.0, 7.0),  # juniper / pinyon
    "joshua": (4.0, 9.0),
    "krummholz": (0.8, 2.2),  # wind-stunted spruce–fir mats at treeline
    "shrub": (0.6, 2.0),  # heath, blueberry, manzanita, willow
    "sage": (0.5, 1.4),  # sagebrush, blackbrush, creosote
    "grass": (0.3, 0.7),  # sedge and grass tussocks
    "boulder": (0.8, 2.6),
}
TREES = frozenset({"broadleaf", "aspen", "spruce", "fir", "pine", "lodgepole", "juniper", "joshua"})
DECIDUOUS = frozenset({"broadleaf", "aspen"})

COVER_NONE, COVER_FOREST, COVER_OPEN, COVER_WETLAND, COVER_ROCK, COVER_SCREE, COVER_GLACIER, COVER_WATER = range(8)
# OSM feature kind -> cover code; a higher code wins where areas overlap
COVER_CODES = {"forest": COVER_FOREST, "open": COVER_OPEN, "wetland": COVER_WETLAND, "bare_rock": COVER_ROCK,
               "scree": COVER_SCREE, "glacier": COVER_GLACIER, "water": COVER_WATER}

ZONE_JITTER_M = 70.0  # zone edges wander this much up and down the slope
CLIFF_SLOPE_DEG = 40.0  # steeper ground is mostly bare rock
PLANTS_PER_SUBCELL = 6  # at density 1 (a few stand for the many real trees)
TRAIL_CLEAR_M = 22.0  # keep the hike's own trail clear, so trees don't hide its line


@dataclass(frozen=True)
class Zone:
    name: str
    top_m: float  # upper edge at the biome's reference latitude
    ground: RGB  # colour of the land seen from above (canopy, tundra, sand, ...)
    flora: Tuple[Tuple[str, float], ...] = ()  # (species, weight)
    density: float = 0.0  # 1 = closed forest

    def tree_share(self) -> float:
        total = sum(w for _s, w in self.flora) or 1.0
        return sum(w for s, w in self.flora if s in TREES) / total

    def share(self, species: frozenset) -> float:
        total = sum(w for _s, w in self.flora) or 1.0
        return sum(w for s, w in self.flora if s in species) / total


@dataclass(frozen=True)
class Biome:
    name: str
    zones: Tuple[Zone, ...]  # low to high; the last one covers everything above
    meadow: Zone  # clearings and meadows ("open" land cover) below treeline
    rock: RGB = (152, 150, 144)  # bare rock and cliffs (granite grey, red sandstone, ...)
    ref_lat: float = 0.0
    lapse_m_per_deg: float = 0.0  # zone edges drop this much per degree north of ref_lat

    def shift_m(self, lat: float) -> float:
        return (lat - self.ref_lat) * self.lapse_m_per_deg

    def zone_at(self, ele_m: float, lat: float) -> Zone:
        ele = ele_m + self.shift_m(lat)
        for z in self.zones:
            if ele < z.top_m:
                return z
        return self.zones[-1]

    def treeline_m(self, lat: float) -> Optional[float]:
        """Top of the highest mostly-wooded zone (None when trees reach every summit)."""
        top = None
        for z in self.zones[:-1]:
            if z.tree_share() >= 0.5 and z.density >= 0.5:
                top = z.top_m
        if top is None or self.zones[-1].tree_share() >= 0.5:
            return None
        return top - self.shift_m(lat)


ALPINE_TUNDRA = Zone("Alpine tundra", 1e9, (134, 132, 94), (("grass", 0.55), ("shrub", 0.2), ("boulder", 0.25)), 0.6)

NORTHEAST = Biome(  # White Mountains, Green Mountains, Adirondacks, Acadia, southern New England
    "Northern Appalachians",
    (
        Zone("Northern hardwood forest", 750, (66, 104, 56),
             (("broadleaf", 0.6), ("fir", 0.25), ("spruce", 0.1), ("pine", 0.05)), 1.0),
        Zone("Spruce–fir forest", 1250, (40, 74, 48), (("spruce", 0.85), ("broadleaf", 0.15)), 1.0),
        Zone("Krummholz", 1450, (66, 92, 60), (("krummholz", 0.65), ("spruce", 0.1), ("shrub", 0.25)), 0.85),
        ALPINE_TUNDRA,
    ),
    meadow=Zone("Meadow", 0, (108, 134, 70), (("grass", 0.6), ("shrub", 0.3), ("broadleaf", 0.1)), 0.5),
    rock=(154, 152, 146), ref_lat=44.2, lapse_m_per_deg=100.0,
)

MONADNOCK = Biome(  # summit burned bare in the 1800s: open ledges far below the climatic treeline
    "Monadnock",
    (
        Zone("Hardwood & hemlock forest", 560, (64, 102, 56), (("broadleaf", 0.55), ("fir", 0.3), ("spruce", 0.15)), 1.0),
        Zone("Red spruce forest", 800, (42, 76, 48), (("spruce", 0.8), ("broadleaf", 0.2)), 0.95),
        Zone("Open summit ledges", 1e9, (150, 146, 132),
             (("grass", 0.35), ("shrub", 0.35), ("krummholz", 0.15), ("boulder", 0.15)), 0.35),
    ),
    meadow=Zone("Meadow", 0, (108, 134, 70), (("grass", 0.6), ("shrub", 0.4)), 0.5),
    rock=(158, 154, 146),
)

SOUTHERN_APPALACHIANS = Biome(  # Smokies, Blue Ridge, Shenandoah: no treeline, spruce–fir on the highest crests
    "Southern Appalachians",
    (
        Zone("Oak–hickory & cove hardwoods", 1400, (62, 102, 52),
             (("broadleaf", 0.8), ("fir", 0.15), ("pine", 0.05)), 1.0),
        Zone("Northern hardwoods", 1650, (64, 104, 56), (("broadleaf", 0.7), ("spruce", 0.2), ("fir", 0.1)), 1.0),
        Zone("Spruce–fir forest", 1e9, (38, 70, 46), (("spruce", 0.9), ("broadleaf", 0.1)), 1.0),
    ),
    meadow=Zone("Grassy bald", 0, (112, 138, 72), (("grass", 0.65), ("shrub", 0.35)), 0.55),
    rock=(150, 146, 136), ref_lat=35.6, lapse_m_per_deg=100.0,
)

ROCKIES = Biome(  # Rocky Mountain NP, Tetons, Yellowstone, Glacier
    "Rocky Mountains",
    (
        Zone("Ponderosa pine woodland", 2700, (100, 112, 72), (("pine", 0.7), ("juniper", 0.1), ("sage", 0.2)), 0.55),
        Zone("Lodgepole pine & aspen", 2900, (70, 98, 58), (("lodgepole", 0.6), ("aspen", 0.3), ("spruce", 0.1)), 0.85),
        Zone("Spruce–fir forest", 3350, (44, 76, 50), (("spruce", 0.9), ("aspen", 0.05), ("shrub", 0.05)), 0.95),
        Zone("Krummholz", 3550, (78, 98, 64), (("krummholz", 0.6), ("spruce", 0.15), ("grass", 0.25)), 0.7),
        Zone("Alpine tundra", 1e9, (138, 136, 98), (("grass", 0.6), ("shrub", 0.1), ("boulder", 0.3)), 0.6),
    ),
    meadow=Zone("Sagebrush meadow", 0, (126, 138, 84), (("grass", 0.5), ("sage", 0.35), ("shrub", 0.15)), 0.6),
    rock=(150, 146, 138), ref_lat=40.3, lapse_m_per_deg=110.0,
)

CASCADES = Biome(  # Mount Rainier, Mount Baker, Olympics
    "Cascades",
    (
        Zone("Douglas-fir & western hemlock forest", 1100, (36, 74, 46),
             (("fir", 0.85), ("broadleaf", 0.1), ("shrub", 0.05)), 1.0),
        Zone("Silver fir & noble fir forest", 1600, (40, 78, 50), (("fir", 0.5), ("spruce", 0.45), ("shrub", 0.05)), 0.95),
        Zone("Subalpine parkland", 1950, (98, 128, 70),
             (("spruce", 0.35), ("fir", 0.1), ("grass", 0.4), ("shrub", 0.15)), 0.55),
        Zone("Alpine heather & rock", 2300, (128, 128, 102), (("grass", 0.4), ("shrub", 0.3), ("boulder", 0.3)), 0.4),
        Zone("Rock & snowfields", 1e9, (176, 176, 174), (("boulder", 1.0),), 0.15),
    ),
    meadow=Zone("Wildflower meadow", 0, (112, 140, 72), (("grass", 0.6), ("shrub", 0.3), ("spruce", 0.1)), 0.6),
    rock=(132, 130, 128), ref_lat=46.8, lapse_m_per_deg=100.0,
)

SIERRA = Biome(  # Yosemite, Sequoia & Kings Canyon, Tahoe
    "Sierra Nevada",
    (
        Zone("Mixed conifer & black oak forest", 1800, (70, 100, 58),
             (("pine", 0.35), ("fir", 0.35), ("broadleaf", 0.2), ("shrub", 0.1)), 0.85),
        Zone("Red fir & Jeffrey pine forest", 2700, (56, 90, 56), (("fir", 0.5), ("pine", 0.4), ("shrub", 0.1)), 0.75),
        Zone("Lodgepole & whitebark pine", 3300, (80, 102, 64), (("lodgepole", 0.6), ("pine", 0.2), ("shrub", 0.2)), 0.55),
        Zone("Krummholz", 3500, (112, 114, 90), (("krummholz", 0.4), ("grass", 0.3), ("shrub", 0.3)), 0.45),
        Zone("Alpine fell-field", 1e9, (152, 148, 128), (("grass", 0.5), ("boulder", 0.5)), 0.35),
    ),
    meadow=Zone("Subalpine meadow", 0, (118, 140, 78), (("grass", 0.7), ("shrub", 0.2), ("lodgepole", 0.1)), 0.6),
    rock=(178, 176, 168), ref_lat=37.7, lapse_m_per_deg=100.0,
)

COLORADO_PLATEAU = Biome(  # Zion, Grand Canyon, Bryce, Arches, Canyonlands
    "Colorado Plateau",
    (
        Zone("Desert scrub", 1500, (192, 152, 112), (("sage", 0.75), ("grass", 0.2), ("juniper", 0.05)), 0.35),
        Zone("Pinyon–juniper woodland", 2100, (152, 132, 96), (("juniper", 0.7), ("sage", 0.3)), 0.45),
        Zone("Ponderosa pine forest", 2500, (98, 110, 70), (("pine", 0.8), ("shrub", 0.1), ("juniper", 0.1)), 0.65),
        Zone("Spruce, fir & aspen forest", 1e9, (64, 92, 58), (("spruce", 0.6), ("aspen", 0.35), ("fir", 0.05)), 0.85),
    ),
    meadow=Zone("Grassland", 0, (170, 156, 104), (("grass", 0.6), ("sage", 0.4)), 0.5),
    rock=(186, 112, 78), ref_lat=36.5, lapse_m_per_deg=80.0,
)

MOJAVE = Biome(  # Joshua Tree, Death Valley margins, Mojave Preserve
    "Mojave Desert",
    (
        Zone("Creosote bush scrub", 1000, (204, 184, 144), (("sage", 0.9), ("grass", 0.1)), 0.3),
        Zone("Joshua tree woodland", 1550, (188, 172, 132), (("joshua", 0.35), ("sage", 0.5), ("juniper", 0.15)), 0.4),
        Zone("Pinyon–juniper woodland", 1e9, (156, 144, 108), (("juniper", 0.65), ("sage", 0.3), ("joshua", 0.05)), 0.45),
    ),
    meadow=Zone("Desert grassland", 0, (192, 176, 128), (("grass", 0.5), ("sage", 0.5)), 0.4),
    rock=(178, 162, 140), ref_lat=34.0, lapse_m_per_deg=80.0,
)

PACK_BIOMES = {"mount_monadnock": MONADNOCK}
# (lon_min, lon_max, lat_min, lat_max) -> biome; first match wins
REGIONS: Tuple[Tuple[Tuple[float, float, float, float], Biome], ...] = (
    ((-118.5, -114.5, 33.0, 36.5), MOJAVE),
    ((-114.5, -107.0, 35.0, 39.0), COLORADO_PLATEAU),
    ((-121.5, -117.5, 35.5, 40.5), SIERRA),
    ((-125.0, -119.5, 42.0, 50.0), CASCADES),
    ((-117.0, -104.0, 35.0, 49.5), ROCKIES),
    ((-90.0, -60.0, 33.0, 39.5), SOUTHERN_APPALACHIANS),
    ((-90.0, -60.0, 39.5, 48.0), NORTHEAST),
)


def biome_for(lat: float, lon: float, pack_id: str = "") -> Biome:
    if pack_id in PACK_BIOMES:
        return PACK_BIOMES[pack_id]
    for (lon0, lon1, lat0, lat1), biome in REGIONS:
        if lon0 <= lon <= lon1 and lat0 <= lat <= lat1:
            return biome
    return NORTHEAST if lon > -100.0 else ROCKIES


def season_for(month: Optional[int], lat: float) -> str:
    """'summer', 'autumn' (fall colours) or 'winter' (bare hardwoods); unknown month = summer."""
    if not month:
        return "summer"
    north = lat >= 40.0
    if month in ((9, 10) if north else (10, 11)):
        return "autumn"
    if month in ((11, 12, 1, 2, 3, 4) if north else (12, 1, 2, 3)):
        return "winter"
    return "summer"


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def zone_ground(zone: Zone, season: str) -> RGB:
    """A zone's ground colour in this season: hardwood canopy turns russet in autumn and grey-brown
    in winter; grassland turns golden."""
    if season == "summer":
        return zone.ground
    leafy = zone.share(DECIDUOUS) * zone.density
    grassy = zone.share(frozenset({"grass"}))
    if season == "autumn":
        return _mix(_mix(zone.ground, (156, 98, 48), leafy * 0.7), (160, 140, 86), grassy * 0.4)
    return _mix(_mix(zone.ground, (120, 110, 96), leafy * 0.75), (150, 136, 104), grassy * 0.5)


def _hash01(ix: int, iy: int, seed: int = 0) -> float:
    h = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0


def value_noise(x: float, y: float, scale_m: float = 350.0, seed: int = 0) -> float:
    """Smooth deterministic noise in 0..1 (wavelength ~scale_m)."""
    gx, gy = x / scale_m, y / scale_m
    ix, iy = math.floor(gx), math.floor(gy)
    tx, ty = gx - ix, gy - iy
    tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
    a, b = _hash01(ix, iy, seed), _hash01(ix + 1, iy, seed)
    c, d = _hash01(ix, iy + 1, seed), _hash01(ix + 1, iy + 1, seed)
    return (a + (b - a) * tx) + ((c + (d - c) * tx) - (a + (b - a) * tx)) * ty


class LandCover:
    """OpenStreetMap land cover rasterised on a grid `step_m` apart covering the mesh."""

    def __init__(self, x0: float, y0: float, step_m: float, nx: int, ny: int) -> None:
        self.x0, self.y0, self.step, self.nx, self.ny = x0, y0, step_m, nx, ny
        self.codes = bytearray(nx * ny)

    @classmethod
    def for_mesh(cls, mesh, sub: int = 2) -> "LandCover":
        return cls(mesh.x0, mesh.y0, mesh.cell_m / sub, (mesh.cols - 1) * sub, (mesh.rows - 1) * sub)

    def code_at(self, x: float, y: float) -> int:
        i, j = int((x - self.x0) // self.step), int((y - self.y0) // self.step)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return self.codes[j * self.nx + i]
        return COVER_NONE

    def fill(self, ring_xy: Sequence[Tuple[float, float]], code: int) -> None:
        """Scanline-fill a polygon; samples are cell centres. Overlaps keep the higher code."""
        n = len(ring_xy)
        if n < 3:
            return
        rows: Dict[int, List[float]] = {}
        x0, y0, step = self.x0, self.y0, self.step
        for k in range(n):
            ax, ay = ring_xy[k]
            bx, by = ring_xy[(k + 1) % n]
            if ay == by:
                continue
            lo, hi = (ay, by) if ay < by else (by, ay)
            j0 = max(0, math.ceil((lo - y0) / step - 0.5))
            j1 = min(self.ny - 1, math.ceil((hi - y0) / step - 0.5) - 1)
            for j in range(j0, j1 + 1):
                yc = y0 + (j + 0.5) * step
                rows.setdefault(j, []).append(ax + (yc - ay) * (bx - ax) / (by - ay))
        codes, nx = self.codes, self.nx
        for j, xs in rows.items():
            xs.sort()
            base = j * nx
            for a, b in zip(xs[::2], xs[1::2]):
                i0 = max(0, math.ceil((a - x0) / step - 0.5))
                i1 = min(nx - 1, math.ceil((b - x0) / step - 0.5) - 1)
                for i in range(i0, i1 + 1):
                    if codes[base + i] < code:
                        codes[base + i] = code

    @classmethod
    def from_pack(cls, pack, mesh) -> "LandCover":
        cover = cls.for_mesh(mesh)
        if pack is None:
            return cover
        frame = mesh.frame
        x1 = mesh.x0 + (mesh.cols - 1) * mesh.cell_m
        y1 = mesh.y0 + (mesh.rows - 1) * mesh.cell_m
        lat_lo, lon_lo = frame.to_latlon(mesh.x0, mesh.y0)
        lat_hi, lon_hi = frame.to_latlon(x1, y1)
        for f in pack.features:
            code = COVER_CODES.get(f.kind)
            if code is None or f.geom != "area" or len(f.coords) < 3:
                continue
            lons = [c[0] for c in f.coords]
            lats = [c[1] for c in f.coords]
            if max(lons) < lon_lo or min(lons) > lon_hi or max(lats) < lat_lo or min(lats) > lat_hi:
                continue
            cover.fill([frame.to_xy(c[1], c[0]) for c in f.coords], code)
        return cover


@dataclass(frozen=True)
class Habitat:
    name: str
    ground: RGB
    flora: Tuple[Tuple[str, float], ...]
    density: float


class Vegetation:
    """What grows where on a terrain mesh: ground colour per mesh cell and plants per cell
    (generated on first use, the same every time)."""

    def __init__(self, mesh, biome: Biome, cover: LandCover, season: str = "summer",
                 avoid_xy: Sequence[Sequence[float]] = (), seed: int = 0) -> None:
        self.mesh, self.biome, self.cover, self.season, self.seed = mesh, biome, cover, season, seed
        self.lat = mesh.frame.lat0
        self.zx = mesh.frame.zx
        self.treeline = biome.treeline_m(self.lat)
        self._sub = max(1, round(mesh.cell_m / cover.step))
        self._cache: Dict[Tuple[int, int], List[Plant]] = {}
        self._habitats: Dict[Tuple[str, int, bool], Habitat] = {}
        # Route points bucketed by cover cell, to keep the trail itself clear
        self._avoid: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
        for p in avoid_xy:
            key = (int((p[0] - cover.x0) // cover.step), int((p[1] - cover.y0) // cover.step))
            self._avoid.setdefault(key, []).append((p[0], p[1]))
        meadow = biome.meadow
        self._cover_habitats = {
            COVER_GLACIER: Habitat("Glacier & snowfield", (238, 242, 246), (), 0.0),
            COVER_SCREE: Habitat("Scree", (168, 165, 158), (("boulder", 1.0),), 0.5),
            COVER_ROCK: Habitat("Bare rock", biome.rock, (("boulder", 0.6), ("shrub", 0.2), ("grass", 0.2)), 0.3),
            COVER_WETLAND: Habitat("Wetland", zone_ground(Zone("", 0, (98, 112, 72), (("grass", 1.0),)), season),
                                   (("grass", 0.7), ("shrub", 0.3)), 0.6),
            COVER_OPEN: Habitat(meadow.name, zone_ground(meadow, season), meadow.flora, meadow.density),
        }

    def _slope_deg(self, r: int, c: int) -> float:
        z, cell = self.mesh.z, self.mesh.cell_m
        dzdx = ((z[r][c + 1] - z[r][c]) + (z[r + 1][c + 1] - z[r + 1][c])) / (2 * cell * self.zx)
        dzdy = ((z[r + 1][c] - z[r][c]) + (z[r + 1][c + 1] - z[r][c + 1])) / (2 * cell * self.zx)
        return math.degrees(math.atan(math.hypot(dzdx, dzdy)))

    def habitat_at(self, x: float, y: float, slope_deg: float = 0.0) -> Tuple[Habitat, float]:
        """(habitat, true elevation in m) at a point."""
        ele = self.mesh.height_at(x, y) / self.zx
        code = self.cover.code_at(x, y)
        zone = self.biome.zone_at(ele + (value_noise(x, y, seed=self.seed) - 0.5) * 2 * ZONE_JITTER_M, self.lat)
        key = (zone.name, code, slope_deg >= CLIFF_SLOPE_DEG)
        hab = self._habitats.get(key)
        if hab is None:
            hab = self._habitats[key] = self._habitat(zone, code, key[2])
        return hab, ele

    def _habitat(self, zone: Zone, code: int, cliff: bool) -> Habitat:
        hab = Habitat(zone.name, zone_ground(zone, self.season), zone.flora, zone.density)
        if code == COVER_WATER:
            return Habitat("Lake", hab.ground, (), 0.0)
        if code in (COVER_GLACIER, COVER_SCREE, COVER_ROCK, COVER_WETLAND):
            return self._cover_habitats[code]
        if code == COVER_OPEN and zone.tree_share() >= 0.3:  # clearings and meadows below treeline
            hab = self._cover_habitats[COVER_OPEN]
        elif code == COVER_FOREST and zone.tree_share() >= 0.3 and zone.density < 0.85:
            hab = Habitat(hab.name, _mix(hab.ground, (56, 88, 54), 0.4), hab.flora, 0.85)
        if cliff:
            hab = Habitat("Cliffs", _mix(hab.ground, self.biome.rock, 0.65),
                          (("boulder", 0.5),) + tuple(hab.flora), hab.density * 0.3)
        return hab

    def ground_rgb(self, r: int, c: int) -> RGB:
        """Mean ground colour of a mesh cell (with a little mottling)."""
        mesh, sub = self.mesh, self._sub
        step = mesh.cell_m / sub
        slope = self._slope_deg(r, c)
        tot = [0, 0, 0]
        for j in range(sub):
            for i in range(sub):
                x = mesh.x0 + c * mesh.cell_m + (i + 0.5) * step
                y = mesh.y0 + r * mesh.cell_m + (j + 0.5) * step
                g = self.habitat_at(x, y, slope)[0].ground
                tot[0] += g[0]
                tot[1] += g[1]
                tot[2] += g[2]
        k = 0.94 + 0.12 * _hash01(r, c, self.seed + 7)
        n = sub * sub
        return (min(255, int(tot[0] / n * k)), min(255, int(tot[1] / n * k)), min(255, int(tot[2] / n * k)))

    def habitat_grid(self) -> Tuple[List[Habitat], bytearray]:
        """Every land-cover sample's habitat (index into the returned list), row by row on the cover
        grid: what the browser replay grows its plants from."""
        mesh, sub = self.mesh, self._sub
        step = mesh.cell_m / sub
        nx = (mesh.cols - 1) * sub
        index: Dict[Habitat, int] = {}
        grid = bytearray(nx * (mesh.rows - 1) * sub)
        for r in range(mesh.rows - 1):
            for c in range(mesh.cols - 1):
                slope = self._slope_deg(r, c)
                for j in range(sub):
                    for i in range(sub):
                        hab = self.habitat_at(mesh.x0 + c * mesh.cell_m + (i + 0.5) * step,
                                              mesh.y0 + r * mesh.cell_m + (j + 0.5) * step, slope)[0]
                        grid[(r * sub + j) * nx + c * sub + i] = index.setdefault(hab, len(index))
        return list(index), grid

    def zone_name_at(self, x: float, y: float) -> str:
        r, c = self.mesh.cell_of(x, y)
        r = max(0, min(self.mesh.rows - 2, r))
        c = max(0, min(self.mesh.cols - 2, c))
        return self.habitat_at(x, y, self._slope_deg(r, c))[0].name

    def _near_trail(self, x: float, y: float) -> bool:
        cv = self.cover
        i, j = int((x - cv.x0) // cv.step), int((y - cv.y0) // cv.step)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for px, py in self._avoid.get((i + di, j + dj), ()):
                    if (px - x) ** 2 + (py - y) ** 2 < TRAIL_CLEAR_M * TRAIL_CLEAR_M:
                        return True
        return False

    def plants(self, r: int, c: int) -> List[Plant]:
        """This mesh cell's plants, in random order (draw a prefix for fewer)."""
        key = (r, c)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        mesh, sub = self.mesh, self._sub
        step = mesh.cell_m / sub
        rng = random.Random((r * 1_000_003 + c) * 31 + self.seed)
        slope = self._slope_deg(r, c)
        out: List[Plant] = []
        for j in range(sub):
            for i in range(sub):
                sx = mesh.x0 + c * mesh.cell_m + i * step
                sy = mesh.y0 + r * mesh.cell_m + j * step
                hab, ele = self.habitat_at(sx + step / 2, sy + step / 2, slope)
                if not hab.flora or hab.density <= 0:
                    continue
                want = hab.density * PLANTS_PER_SUBCELL
                count = int(want) + (1 if rng.random() < want - int(want) else 0)
                species = [s for s, _w in hab.flora]
                weights = [w for _s, w in hab.flora]
                for _ in range(count):
                    x, y = sx + rng.random() * step, sy + rng.random() * step
                    sp = rng.choices(species, weights)[0]
                    if self.cover.code_at(x, y) == COVER_WATER or self._near_trail(x, y):
                        continue
                    lo, hi = SPECIES_HEIGHT_M[sp]
                    height = lo + (hi - lo) * rng.random()
                    if sp in TREES and self.treeline is not None and ele > self.treeline - 200.0:
                        height *= max(0.35, 0.45 + 0.55 * (self.treeline - ele) / 200.0)  # stunted near treeline
                    out.append((x, y, mesh.height_at(x, y), sp, rng.randrange(4), height))
        rng.shuffle(out)
        self._cache[key] = out
        return out


def build_vegetation(pack, mesh, avoid_xy: Sequence[Sequence[float]] = (), month: Optional[int] = None) -> Vegetation:
    """Biome, season and land cover for `pack` (a DestinationPack or None) on `mesh`."""
    frame = mesh.frame
    biome = biome_for(frame.lat0, frame.lon0, getattr(pack, "id", "") or "")
    return Vegetation(mesh, biome, LandCover.from_pack(pack, mesh), season_for(month, frame.lat0), avoid_xy)
