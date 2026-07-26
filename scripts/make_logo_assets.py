"""One-off conversion: dokey_logo.png (flattened against a baked-in checker
pattern, no real alpha) -> dokey/assets/logo.png (true transparency) and
logo.ico (multi-size, for the Windows pywebview window icon).

The source's "transparent" background was flattened to an actual checkerboard
of near-white/light-gray pixels rather than an alpha channel. The gold mark is
saturated (R>G>B); the checker background is achromatic (R=G=B). That gap is
wide enough to threshold on saturation and recover clean alpha.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "dokey_logo.png"
OUT_DIR = REPO_ROOT / "dokey" / "assets"

SAT_LOW = 8    # below this: fully transparent (background)
SAT_HIGH = 40  # above this: fully opaque (mark)


def to_transparent(im: Image.Image) -> Image.Image:
    rgb = im.convert("RGB")
    pixels = rgb.load()
    w, h = rgb.size
    out = Image.new("RGBA", (w, h))
    out_px = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            sat = max(r, g, b) - min(r, g, b)
            if sat <= SAT_LOW:
                alpha = 0
            elif sat >= SAT_HIGH:
                alpha = 255
            else:
                alpha = round((sat - SAT_LOW) * 255 / (SAT_HIGH - SAT_LOW))
            out_px[x, y] = (r, g, b, alpha)
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    im = Image.open(SRC)
    transparent = to_transparent(im)
    bbox = transparent.getbbox()
    if bbox:
        pad = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) // 20
        left, upper, right, lower = bbox
        left = max(0, left - pad)
        upper = max(0, upper - pad)
        right = min(transparent.width, right + pad)
        lower = min(transparent.height, lower + pad)
        transparent = transparent.crop((left, upper, right, lower))

    logo = transparent.resize((512, 512), Image.LANCZOS)
    logo.save(OUT_DIR / "logo.png")

    logo.save(
        OUT_DIR / "logo.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
    )
    print(f"wrote {OUT_DIR / 'logo.png'}")
    print(f"wrote {OUT_DIR / 'logo.ico'}")


if __name__ == "__main__":
    main()
