"""Crop, rotate, adjust and filter a photo — without losing the one you took.

The rule the whole module is built around: **the file the device sent is never
overwritten.** Editing is something you do to a copy, for ever and reversibly,
because the one irreplaceable thing here is the original frame. Google Photos
works this way too, and for the same reason: people crop a photo, come back a
year later, and want the rest of it.

WHAT IS NOT RECOMPUTED, AND WHY IT MATTERS MORE THAN IT LOOKS

`content_hash` stays exactly as it was. It is not a checksum of the file on
disk — it is the identity of the UPLOAD. The phone's backup asks "which of
these do you already have?" by that hash before sending a byte, and the
duplicate finder pairs photos with it. Recomputing it on every edit would mean
a phone no longer recognising its own uploads and re-sending the entire camera
roll, which is the same trap the EXIF-transpose comment in gallery.py
describes one layer down.

`phash` IS recomputed, and the asymmetry is deliberate. phash describes what
the picture LOOKS like, and after a crop it genuinely looks like something
else; leaving it stale would have the near-duplicate finder offering to delete
a photo on the strength of a resemblance that no longer exists.

`width`/`height` are recomputed too — they are the DISPLAYED size and the
justified timeline lays rows out from them, so a cropped photo that still
reports its old shape is given the wrong slot and visibly jumps.
"""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance, ImageOps

#: Stored copy of the untouched original, kept beside it under the same name
#: plus this suffix. A separate variant directory was the alternative; this
#: keeps a photo's files together, so anything that walks one user's originals
#: sees the pristine copy too rather than silently missing it from a storage
#: total.
PRISTINE_SUFFIX = ".orig"

#: Edited photos are re-encoded once, at a quality high enough that a second
#: edit of an edit does not visibly accumulate artefacts. Edits always start
#: from the PRISTINE file, never from the last edited one, so in practice
#: there is only ever one generation of loss — but somebody will eventually
#: change that, and 92 is cheap insurance.
JPEG_QUALITY = 92

#: An edit that asks for a crop smaller than this, in either direction, is
#: refused. A zero-width crop is a corrupt file, and a two-pixel one is a
#: mis-drag nobody meant.
MIN_CROP_PX = 16

#: Adjustments are multipliers on PIL's enhancers, clamped. 1.0 is unchanged.
#: The range stops at half and double because past that the control stops
#: being an adjustment and starts being a filter with no name.
ADJUST_MIN, ADJUST_MAX = 0.5, 2.0

#: The named looks. Each is a plain function of an RGB image, so adding one is
#: adding a function — there is no filter engine to learn. Values were picked
#: by eye against photographs, not derived.
def _mono(im):
    return ImageOps.grayscale(im).convert("RGB")


def _sepia(im):
    g = ImageOps.grayscale(im)
    # A warm ramp applied to luminance. Tinting the colour image instead keeps
    # the original hues fighting the tone, which reads as a broken filter
    # rather than as sepia.
    return ImageOps.colorize(g, black="#2b1d0e", white="#ffe7c4").convert("RGB")


def _vivid(im):
    return ImageEnhance.Color(im).enhance(1.45)


def _fade(im):
    # Lift the blacks: the look is low contrast plus a raised floor, and
    # contrast alone just makes it grey.
    faded = ImageEnhance.Contrast(im).enhance(0.75)
    return Image.blend(faded, Image.new("RGB", im.size, (255, 246, 235)), 0.12)


def _warm(im):
    r, g, b = im.split()
    r = r.point(lambda v: min(255, int(v * 1.08)))
    b = b.point(lambda v: int(v * 0.93))
    return Image.merge("RGB", (r, g, b))


def _cool(im):
    r, g, b = im.split()
    r = r.point(lambda v: int(v * 0.93))
    b = b.point(lambda v: min(255, int(v * 1.08)))
    return Image.merge("RGB", (r, g, b))


def _noir(im):
    return ImageEnhance.Contrast(ImageOps.grayscale(im).convert("RGB")).enhance(1.35)


FILTERS = {
    "none": None,
    "mono": _mono,
    "noir": _noir,
    "sepia": _sepia,
    "vivid": _vivid,
    "fade": _fade,
    "warm": _warm,
    "cool": _cool,
}


class EditError(ValueError):
    """A request that cannot be honoured, with a sentence for the person."""


def normalise(raw: dict | None) -> dict:
    """Validate an edit request and return it in canonical form.

    Every value is clamped rather than rejected where clamping has an obvious
    meaning, and rejected where it does not. A slider that silently saturates
    is better than an error nobody can act on; a crop that falls outside the
    picture is a bug in the caller and saying so is the only useful answer.
    """
    raw = raw or {}
    out: dict = {}

    rot = raw.get("rotate") or 0
    try:
        rot = int(rot) % 360
    except (TypeError, ValueError):
        raise EditError("Rotation must be a number of degrees")
    if rot not in (0, 90, 180, 270):
        raise EditError("Rotation must be 0, 90, 180 or 270 degrees")
    if rot:
        out["rotate"] = rot

    if raw.get("flip"):
        out["flip"] = True

    crop = raw.get("crop")
    if crop:
        try:
            x, y, w, h = (float(crop["x"]), float(crop["y"]),
                          float(crop["w"]), float(crop["h"]))
        except (KeyError, TypeError, ValueError):
            raise EditError("A crop needs x, y, w and h")
        # Fractions of the picture, not pixels. The browser knows the photo's
        # displayed size and nothing else; sending pixels would mean the
        # client and the server disagreeing about which size that was.
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise EditError("A crop must be given as fractions between 0 and 1")
        if x + w > 1.0001 or y + h > 1.0001:
            raise EditError("That crop falls outside the photo")
        out["crop"] = {"x": x, "y": y, "w": min(w, 1 - x), "h": min(h, 1 - y)}

    for key in ("brightness", "contrast", "saturation", "sharpness"):
        if key in raw and raw[key] is not None:
            try:
                v = float(raw[key])
            except (TypeError, ValueError):
                raise EditError(f"{key} must be a number")
            v = max(ADJUST_MIN, min(ADJUST_MAX, v))
            if abs(v - 1.0) > 0.001:
                out[key] = round(v, 3)

    f = (raw.get("filter") or "none").strip().lower()
    if f not in FILTERS:
        raise EditError(f"There is no filter called {f!r}")
    if f != "none":
        out["filter"] = f

    return out


def is_noop(edit: dict) -> bool:
    return not edit


_ENHANCERS = {
    "brightness": ImageEnhance.Brightness,
    "contrast": ImageEnhance.Contrast,
    "saturation": ImageEnhance.Color,
    "sharpness": ImageEnhance.Sharpness,
}


def apply(raw_bytes: bytes, edit: dict) -> tuple[bytes, int, int]:
    """Render an edit. Returns the new JPEG, and its width and height.

    Always applied to the PRISTINE bytes, in a fixed order: orient, rotate,
    flip, crop, adjust, filter. The order is not arbitrary — a crop is given
    in fractions of the picture as the person saw it, so it has to happen
    after the rotation that decided which way up that was, and before the
    colour work, which does not care about geometry.
    """
    im = Image.open(io.BytesIO(raw_bytes))
    # The same transpose the thumbnailer does: the stored original carries the
    # sensor frame plus an orientation tag, and a crop drawn on the upright
    # picture means nothing against the sideways one.
    im = ImageOps.exif_transpose(im) or im
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")

    if edit.get("rotate"):
        # expand=True so a 90 turn gives a taller picture rather than cropping
        # it to the old frame.
        im = im.rotate(-int(edit["rotate"]), expand=True)

    if edit.get("flip"):
        im = ImageOps.mirror(im)

    if edit.get("crop"):
        c = edit["crop"]
        w, h = im.size
        left = int(round(c["x"] * w))
        top = int(round(c["y"] * h))
        right = int(round((c["x"] + c["w"]) * w))
        bottom = int(round((c["y"] + c["h"]) * h))
        if right - left < MIN_CROP_PX or bottom - top < MIN_CROP_PX:
            raise EditError("That crop is too small to keep")
        im = im.crop((left, top, right, bottom))

    for key, cls in _ENHANCERS.items():
        if key in edit:
            im = cls(im).enhance(float(edit[key]))

    fn = FILTERS.get(edit.get("filter") or "none")
    if fn is not None:
        im = fn(im)

    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue(), im.width, im.height


def describe(edit: dict) -> str:
    """The edit in words, for the audit line and the revert prompt."""
    bits = []
    if edit.get("rotate"):
        bits.append(f"rotated {edit['rotate']}°")
    if edit.get("flip"):
        bits.append("flipped")
    if edit.get("crop"):
        bits.append("cropped")
    for key in ("brightness", "contrast", "saturation", "sharpness"):
        if key in edit:
            bits.append(f"{key} {edit[key]:g}×")
    if edit.get("filter"):
        bits.append(str(edit["filter"]))
    return ", ".join(bits) or "no change"
