"""Shrink the raw 2x captures into web-sized assets for the storefront.

410 KB per screenshot is fine on a fast connection and rude on a phone. These are
resized to the largest size they are ever displayed at, then saved as WebP, which
holds UI screenshots (flat colour, sharp text) far better than JPEG at the same
weight and is supported everywhere that matters now.
"""
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent / "shots"
DST = Path(__file__).resolve().parents[2] / "storefront" / "img"
DST.mkdir(parents=True, exist_ok=True)

# name -> width it is actually rendered at, doubled for retina
WANT = {
    "dashboard": 1200,
    "gallery": 1000,
    "documents": 1000,
    "expenses": 1000,
    "vault": 1000,
    "investments": 1000,
    "insurance": 1000,
    "phone-home": 420,
}

total_before = total_after = 0
for name, w in WANT.items():
    src = SRC / f"{name}.png"
    if not src.is_file():
        print("  missing:", name)
        continue
    im = Image.open(src).convert("RGB")
    target = w * 2
    if im.width > target:
        im = im.resize((target, round(im.height * target / im.width)), Image.LANCZOS)
    out = DST / f"{name}.webp"
    im.save(out, "WEBP", quality=82, method=6)
    total_before += src.stat().st_size
    total_after += out.stat().st_size
    print(f"  {name:14} {im.width}x{im.height}  "
          f"{src.stat().st_size // 1024} KB -> {out.stat().st_size // 1024} KB")

print(f"\ntotal {total_before // 1024} KB -> {total_after // 1024} KB")
