"""Trail photo storage, thumbnail generation, and optional EXIF stripping."""
import hashlib
import math
from functools import lru_cache
from io import BytesIO
import logging
from pathlib import Path
from typing import Optional, Tuple

from . import paths

logger = logging.getLogger(__name__)


def save_trail_photo(
    image_bytes: bytes,
    ext: str = "jpg",
    strip_metadata: bool = False,
) -> Tuple[Path, Optional[Path]]:
    """Save a trail photo into ~/.aegishiker/photos/ and create a 300px thumbnail.

    Returns (full_photo_path, thumbnail_path).
    """
    paths.ensure_app_dirs()
    digest = hashlib.sha256(image_bytes).hexdigest()[:14]
    clean_ext = ext.lower().lstrip(".") or "jpg"
    if clean_ext not in ("jpg", "jpeg", "png", "webp"):
        clean_ext = "jpg"

    photo_path = paths.PHOTOS_DIR / f"trail_{digest}.{clean_ext}"
    thumb_path = paths.PHOTOS_DIR / f"trail_{digest}_300.jpg"

    try:
        from PIL import Image, ImageOps

        img = Image.open(BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        if strip_metadata:
            rgb = img.convert("RGB")
            rgb.save(photo_path, format="JPEG", quality=90)
        else:
            photo_path.write_bytes(image_bytes)

        thumb = img.convert("RGB")
        thumb.thumbnail((300, 300))
        thumb.save(thumb_path, format="JPEG", quality=82)
        return (photo_path, thumb_path)
    except Exception as e:
        logger.debug(f"Pillow processing fallback ({e}); writing raw bytes.")
        photo_path.write_bytes(image_bytes)
        return (photo_path, None)



GPS_IFD = 0x8825
GPS_IMG_DIRECTION = 17


@lru_cache(maxsize=512)
def photo_direction_deg(path: str) -> Optional[float]:
    """Compass bearing (degrees, 0 = north, clockwise) the camera faced, from the photo's EXIF
    GPSImgDirection, or None when the photo doesn't record it."""
    if not path:
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            value = img.getexif().get_ifd(GPS_IFD).get(GPS_IMG_DIRECTION)
        deg = float(value)
    except Exception:
        return None
    return deg % 360.0 if math.isfinite(deg) else None
