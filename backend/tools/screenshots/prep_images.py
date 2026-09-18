"""Crop and shrink the raw 2x captures (pass a theme name to pick the set) into web-sized assets for the storefront.

Two jobs.

CROP. Every capture carries the app's left navigation -- Users & Admin,
Licences, Masters, Email/SMTP -- which is exactly 20.5% of the frame and is
internal chrome nobody is evaluating. It also appeared in all three tour
sections AND all seven tabs, so a visitor saw the same menu nine times. Each
section already names the feature it is showing, so the nav is pure repetition:
cropping it makes the actual content 26% wider at the same display size, which
is the difference between legible and not.

The DASHBOARD keeps its nav on purpose. It is the one shot whose subject is the
whole application rather than one screen, so the module list is the content
there.

SHRINK. 500 KB per screenshot is fine on a fast connection and rude on a phone.
Resized to the largest size each is ever displayed at, then WebP, which holds UI
screenshots (flat colour, sharp text) far better than JPEG at the same weight.
"""
import sys
from pathlib import Path

from PIL import Image

THEME = sys.argv[1] if len(sys.argv) > 1 else "light"
SRC = Path(__file__).resolve().parent / ("shots" if THEME == "light" else f"shots-{THEME}")
DST = Path(__file__).resolve().parents[2] / "storefront" / "img"
DST.mkdir(parents=True, exist_ok=True)

# The left navigation is a FIXED CSS width (263px), not a share of the frame,
# so it is cropped as device pixels: 263 * the capture's scale factor of 2.
# Measuring it as a percentage was wrong the moment the capture viewport
# changed.
NAV_PX = 526

# EVERY shot loses the navigation, the dashboard included. Keeping it there
# was a mistake: the dashboard is the first tab a visitor opens, so the one
# image most people see was the one still showing "Users & Admin", "Licences",
# "Masters" and "Email / SMTP" -- internal administration, not the product.
CROP_NAV = {"dashboard", "gallery", "documents", "expenses",
            "vault", "investments", "insurance"}

# Dropping a fifth of the width takes the frame from 1.49 to 1.18, which beside
# a column of text is a tall thin slab. Trimming the bottom back to a landscape
# aspect also leaves a partial row showing, which is the ordinary way of saying
# "there is more below this".
ASPECT = 1.45

# name -> width it is actually rendered at, doubled for retina
WANT = {
    "dashboard": 1140,
    "gallery": 1140,
    "documents": 1140,
    "expenses": 1140,
    "vault": 1140,
    "investments": 1140,
    "insurance": 1140,
    "phone-home": 430,
}

total_before = total_after = 0
for name, w in WANT.items():
    src = SRC / f"{name}.png"
    if not src.is_file():
        print("  missing:", name)
        continue
    im = Image.open(src).convert("RGB")
    if name in CROP_NAV:
        im = im.crop((NAV_PX, 0, im.width, im.height))
        keep = min(im.height, round(im.width / ASPECT))
        im = im.crop((0, 0, im.width, keep))
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
