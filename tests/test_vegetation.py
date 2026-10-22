"""Region-authentic life zones, land cover and plants for the 3D replay."""
from src.hiking.models import DestinationPack, MapFeature
from src.hiking.terrain3d import LocalFrame, build_mesh
from src.hiking.vegetation import (
    COLORADO_PLATEAU,
    COVER_GLACIER,
    COVER_ROCK,
    COVER_WATER,
    MONADNOCK,
    NORTHEAST,
    ROCKIES,
    SIERRA,
    TREES,
    LandCover,
    Vegetation,
    biome_for,
    build_vegetation,
    season_for,
    zone_ground,
)


def test_biomes_by_region_and_zones_by_elevation_and_latitude() -> None:
    assert biome_for(44.27, -71.30) is NORTHEAST  # White Mountains
    assert biome_for(42.86, -72.11, "mount_monadnock") is MONADNOCK
    assert biome_for(37.25, -112.95) is COLORADO_PLATEAU  # Zion
    assert biome_for(37.74, -119.57) is SIERRA  # Yosemite
    assert biome_for(43.74, -110.80) is ROCKIES  # Tetons

    wm = 44.27
    assert NORTHEAST.zone_at(500, wm).name == "Northern hardwood forest"
    assert NORTHEAST.zone_at(1000, wm).name == "Spruce–fir forest"
    assert NORTHEAST.zone_at(1380, wm).name == "Krummholz"
    assert NORTHEAST.zone_at(1850, wm).name == "Alpine tundra"
    # Treeline is lower farther north: 3,400 m is still forest in Colorado but krummholz in the Tetons
    assert ROCKIES.zone_at(3300, 40.3).name == "Spruce–fir forest"
    assert ROCKIES.zone_at(3300, 43.7).name == "Alpine tundra"
    assert 1235 < NORTHEAST.treeline_m(wm) < 1250  # where krummholz begins
    assert COLORADO_PLATEAU.treeline_m(37.2) is None  # forest tops the plateau


def test_seasons_change_foliage_and_ground() -> None:
    assert season_for(None, 44.0) == "summer"
    assert season_for(10, 44.0) == "autumn" and season_for(10, 35.6) == "autumn"
    assert season_for(9, 35.6) == "summer", "fall colour comes later in the south"
    assert season_for(1, 44.0) == "winter"
    hardwoods = NORTHEAST.zones[0]
    assert zone_ground(hardwoods, "autumn")[0] > hardwoods.ground[0] + 30, "russet canopy"
    spruce = NORTHEAST.zones[1]
    assert zone_ground(spruce, "autumn") == zone_ground(spruce, "summer") or \
        abs(zone_ground(spruce, "autumn")[0] - spruce.ground[0]) < 15, "evergreens barely change"


def _ring(lon0: float, lat0: float, d: float):
    return [[lon0 - d, lat0 - d], [lon0 + d, lat0 - d], [lon0 + d, lat0 + d], [lon0 - d, lat0 + d], [lon0 - d, lat0 - d]]


def test_land_cover_overrides_zones_and_plants_respect_it() -> None:
    frame = LocalFrame(44.0, -71.3)
    mesh = build_mesh(None, frame, (-1200, -1200, 1200, 1200), cell_m=120.0, fallback_ele=600.0)  # hardwood zone
    pack = DestinationPack(id="t", name="t", features=[
        MapFeature(kind="water", geom="area", coords=_ring(-71.3, 44.0, 0.002)),  # pond in the middle
        MapFeature(kind="glacier", geom="area", coords=_ring(-71.29, 44.0, 0.0025)),
        MapFeature(kind="bare_rock", geom="area", coords=_ring(-71.3, 44.009, 0.001)),
    ])
    cover = LandCover.from_pack(pack, mesh)
    assert cover.code_at(0, 0) == COVER_WATER
    x, y = frame.to_xy(44.0, -71.29)
    assert cover.code_at(x, y) == COVER_GLACIER
    rx, ry = frame.to_xy(44.009, -71.3)
    assert cover.code_at(rx, ry) == COVER_ROCK
    assert cover.code_at(1000, -1000) == 0

    veg = Vegetation(mesh, NORTHEAST, cover, avoid_xy=[(800.0, 800.0)])
    assert veg.zone_name_at(1000, -1000) == "Northern hardwood forest"
    assert veg.zone_name_at(0, 0) == "Lake"
    assert veg.zone_name_at(x, y) == "Glacier & snowfield"
    r, c = mesh.cell_of(0, 0)
    assert all(cover.code_at(p[0], p[1]) != COVER_WATER for p in veg.plants(r, c)), "nothing grows on the pond"
    gr, gc = mesh.cell_of(x, y)
    assert veg.ground_rgb(gr, gc)[2] > 200, "glacier cells are white"
    forest = veg.plants(*mesh.cell_of(1000, -1000))
    assert len(forest) >= 12 and sum(p[3] in TREES for p in forest) >= len(forest) * 0.8
    assert forest == veg.plants(*mesh.cell_of(1000, -1000)), "same plants every time"
    near = veg.plants(*mesh.cell_of(800, 800))
    assert all((p[0] - 800) ** 2 + (p[1] - 800) ** 2 >= 22 ** 2 for p in near), "the trail is kept clear"


def test_no_trees_above_treeline_and_stunted_below_it() -> None:
    frame = LocalFrame(44.27, -71.3)
    alpine = build_mesh(None, frame, (-600, -600, 600, 600), cell_m=120.0, fallback_ele=1800.0)
    veg = build_vegetation(None, alpine)
    plants = [p for r in range(alpine.rows - 1) for c in range(alpine.cols - 1) for p in veg.plants(r, c)]
    assert plants and not any(p[3] in TREES for p in plants), "alpine tundra: grass, heath and rocks"
    assert {"grass", "shrub"} <= {p[3] for p in plants}

    high = build_mesh(None, frame, (-600, -600, 600, 600), cell_m=120.0, fallback_ele=1240.0)
    low = build_mesh(None, frame, (-600, -600, 600, 600), cell_m=120.0, fallback_ele=900.0)
    spruce_h = lambda m: [p[5] for r in range(m.rows - 1) for c in range(m.cols - 1)  # noqa: E731
                          for p in build_vegetation(None, m).plants(r, c) if p[3] == "spruce"]
    assert max(spruce_h(high)) < sum(spruce_h(low)) / len(spruce_h(low)), "spruce shrinks toward treeline"
