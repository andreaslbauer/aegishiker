"""Trail photo storage, thumbnail generation, and optional EXIF stripping."""
import hashlib
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

