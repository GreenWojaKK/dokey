"""Turn an embedded sheet image into the pixels the sheet displays."""
from __future__ import annotations

from io import BytesIO

from PIL import Image, UnidentifiedImageError


def crop_image(data: bytes, crop: dict | None) -> bytes | None:
    """Return a cropped copy, or ``None`` when no safe crop can be made."""
    crop = crop or {}
    try:
        left, top, right, bottom = (
            float(crop.get(side) or 0) for side in ("l", "t", "r", "b")
        )
    except (AttributeError, TypeError, ValueError):
        return None
    visible_w = 1 - left - right
    visible_h = 1 - top - bottom
    if (
        any(value < 0 or value > 1 for value in (left, top, right, bottom))
        or visible_w <= 0
        or visible_h <= 0
        or not any((left, top, right, bottom))
    ):
        return None

    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image_format = source.format
            if not image_format:
                return None
            width, height = source.size
            box = (
                int(width * left + 0.5),
                int(height * top + 0.5),
                int(width * (1 - right) + 0.5),
                int(height * (1 - bottom) + 0.5),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                return None
            visible = source.crop(box)
            metadata = {
                key: source.info[key]
                for key in ("icc_profile", "exif", "dpi")
                if source.info.get(key) is not None
            }
            if image_format.upper() in ("JPEG", "JPG"):
                metadata.update(quality=95, subsampling=0)
            output = BytesIO()
            visible.save(output, format=image_format, **metadata)
            return output.getvalue()
    except (OSError, UnidentifiedImageError, ValueError):
        return None
