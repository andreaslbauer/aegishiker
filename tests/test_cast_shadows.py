"""Cast shadows in the 3D replays: hills shade the land behind them from a low sun."""
import base64
import math

from src.hiking.terrain3d import LocalFrame, MeshShader, build_mesh


def _ridge_mesh():
    """Flat land at 300 m with a 400 m high north-south wall at x = 0."""
    frame = LocalFrame(44.0, -71.0, 1.5)
    mesh = build_mesh(None, frame, (-3000, -1500, 3000, 1500), cell_m=100.0, fallback_ele=300.0)
    for r in range(mesh.rows):
        for c in range(mesh.cols):
            x = mesh.x0 + c * mesh.cell_m
            mesh.z[r][c] = (700.0 if abs(x) < 120 else 300.0) * frame.zx
    return mesh


def _sun(az_deg: float, el_deg: float):
    az, el = math.radians(az_deg), math.radians(el_deg)
    return (math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el))


def test_a_ridge_shades_the_land_away_from_a_low_sun() -> None:
    mesh = _ridge_mesh()
    shader = MeshShader(mesh, lambda r, c: (120, 140, 100))
    mid = (mesh.rows - 1) // 2

    def shaded_at(mask, x: float) -> bool:
        c = int((x - mesh.x0) // mesh.cell_m)
        return mask[mid * shader.cols + c] == 1

    evening = shader.shadows(_sun(270, 10))  # sun in the west, 10° up: 400 m wall, ~2.3 km shadow east
    assert shaded_at(evening, 500) and shaded_at(evening, 2000)
    assert not shaded_at(evening, 2900) and not shaded_at(evening, -1000)
    morning = shader.shadows(_sun(90, 10))
    assert shaded_at(morning, -1000) and not shaded_at(morning, 1000)
    assert shader.shadows(_sun(270, 89.5)) is None and shader.shadows(_sun(270, -3)) is None
    lit = shader.colors(_sun(270, 10), 0.45, 0.85, 0.0, None)
    dark = shader.colors(_sun(270, 10), 0.45, 0.85, 0.0, evening)
    c = int((1000 - mesh.x0) // mesh.cell_m)
    assert dark[mid][c] == tuple(int(v * 0.45) for v in (120, 140, 100)) and lit[mid][c] > dark[mid][c]


def test_website_ships_the_shadow_masks() -> None:
    from src.core.hike_site import _cast_bits

    mask = bytearray([0] * 20)
    mask[3] = mask[9] = 1
    packed = base64.b64decode(_cast_bits(mask))
    assert packed[0] == 0b1000 and packed[1] == 0b10
    assert _cast_bits(bytearray(5)) is None and _cast_bits(None) is None
