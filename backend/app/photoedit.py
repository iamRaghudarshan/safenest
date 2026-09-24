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


#: Markup colours, by name. A fixed palette rather than free-form strings:
#: PIL will happily parse "rebeccapurple" and then raise on "#gg0000", and an
#: editor that 500s because of a colour is an editor nobody trusts. Names also
#: survive a round trip through JSON without anybody quoting a hash.
MARKUP_COLOURS = {
    "red": (229, 57, 53), "orange": (245, 124, 0), "yellow": (253, 216, 53),
    "green": (67, 160, 71), "blue": (30, 136, 229), "purple": (142, 68, 173),
    "black": (24, 24, 27), "white": (255, 255, 255),
}

#: What can be drawn. `redact` is not decoration: the reason a household app
#: needs markup at all is usually covering an account number before sending a
#: photo of a bill to somebody.
MARKUP_TOOLS = ("pen", "highlight", "arrow", "rect", "ellipse", "text", "redact")

#: Caps. An edit is stored as JSON on the row and re-rendered on every save,
#: so an unbounded stroke list is both a large column and a slow render.
MAX_MARKUP_OPS = 80
MAX_POINTS = 600
MAX_TEXT_CHARS = 120

#: Stroke width, as a fraction of the picture's short edge. Stored relative so
#: a line drawn on a phone preview is the same THICKNESS on the full-size
#: render — a width in pixels would come out hairline on a 12-megapixel photo.
MARKUP_MIN_W, MARKUP_MAX_W = 0.002, 0.06


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

    marks = raw.get("markup")
    if marks:
        if not isinstance(marks, list):
            raise EditError("Markup must be a list of marks")
        out["markup"] = [_one_mark(m) for m in marks[:MAX_MARKUP_OPS]]
        # A markup list that validated down to nothing is not markup.
        out["markup"] = [m for m in out["markup"] if m]
        if not out["markup"]:
            del out["markup"]

    f = (raw.get("filter") or "none").strip().lower()
    if f not in FILTERS:
        raise EditError(f"There is no filter called {f!r}")
    if f != "none":
        out["filter"] = f

    return out


def _frac(v, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise EditError(f"{what} must be a number")
    # Clamped rather than rejected: a stroke dragged past the edge of the
    # picture is a normal gesture, not a mistake, and refusing the whole mark
    # because one point went over the line would lose the drawing.
    return max(0.0, min(1.0, f))


def _one_mark(m) -> dict | None:
    """Validate one mark, or None if there is nothing left of it."""
    if not isinstance(m, dict):
        return None
    tool = str(m.get("t") or "").strip().lower()
    if tool not in MARKUP_TOOLS:
        raise EditError(f"There is no markup tool called {tool!r}")

    colour = str(m.get("c") or "red").strip().lower()
    if colour not in MARKUP_COLOURS:
        raise EditError(f"There is no colour called {colour!r}")

    try:
        width = float(m.get("w", 0.006))
    except (TypeError, ValueError):
        raise EditError("Stroke width must be a number")
    out = {"t": tool, "c": colour,
           "w": round(max(MARKUP_MIN_W, min(MARKUP_MAX_W, width)), 4)}

    if tool == "text":
        text = str(m.get("text") or "").strip()[:MAX_TEXT_CHARS]
        if not text:
            return None          # an empty label is not a mark
        out["text"] = text
        out["p"] = [[_frac(m.get("x"), "x"), _frac(m.get("y"), "y")]]
        return out

    pts = m.get("p") or []
    if not isinstance(pts, list):
        raise EditError("A mark needs a list of points")
    clean = []
    for pt in pts[:MAX_POINTS]:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        clean.append([_frac(pt[0], "x"), _frac(pt[1], "y")])
    # A shape needs two corners; a stroke needs two points to be a line. One
    # point is a tap, and a tap is how somebody dismisses a tool.
    if len(clean) < 2:
        return None
    out["p"] = clean
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

    # Markup goes on LAST, over everything.
    #
    # Its coordinates are fractions of the picture the person was looking at,
    # which is the one that has already been rotated and cropped — so drawing
    # it before the geometry would put every mark in the wrong place, and
    # drawing it before the filter would have a sepia wash recolour the
    # arrows somebody drew in red.
    if edit.get("markup"):
        im = _draw_markup(im, edit["markup"])

    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue(), im.width, im.height


def _draw_markup(im: "Image.Image", marks: list) -> "Image.Image":
    from PIL import ImageDraw, ImageFilter, ImageFont

    W, H = im.size
    short = min(W, H)
    # A separate RGBA layer, composited once. Drawing highlights straight onto
    # the photo cannot be translucent, and compositing per mark would darken
    # every overlap into a different colour than the one chosen.
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    for m in marks:
        rgb = MARKUP_COLOURS.get(m.get("c"), MARKUP_COLOURS["red"])
        w = max(1, int(round(float(m.get("w", 0.006)) * short)))
        pts = [(p[0] * W, p[1] * H) for p in (m.get("p") or [])]
        if not pts:
            continue
        tool = m.get("t")

        if tool == "redact":
            # Not a black rectangle drawn over the pixels — actually destroys
            # them. A box that merely covers something survives being undone
            # by anyone who opens the file in another editor, which is the
            # opposite of what somebody redacting an account number wants.
            box = _box(pts, W, H)
            if box:
                im.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]),
                                   (20, 20, 22)), box[:2])
            continue

        if tool == "text":
            x, y = pts[0]
            size = max(12, int(round(float(m.get("w", 0.02)) * short * 6)))
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:
                font = ImageFont.load_default()
            # A dark outline under the glyphs, so white text stays readable on
            # a white sky and black text on a black coat.
            d.text((x, y), m.get("text", ""), font=font, fill=rgb + (255,),
                   stroke_width=max(1, size // 14), stroke_fill=(0, 0, 0, 170))
            continue

        if tool == "highlight":
            # Translucent and blunt-ended, like a marker pen.
            d.line(pts, fill=rgb + (95,), width=max(w, short // 90), joint="curve")
        elif tool == "pen":
            d.line(pts, fill=rgb + (255,), width=w, joint="curve")
        elif tool == "arrow":
            _arrow(d, pts[0], pts[-1], rgb, w)
        elif tool == "rect":
            box = _box(pts, W, H)
            if box:
                d.rectangle(box, outline=rgb + (255,), width=w)
        elif tool == "ellipse":
            box = _box(pts, W, H)
            if box:
                d.ellipse(box, outline=rgb + (255,), width=w)

    if layer.getbbox() is None:
        return im
    base = im.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


def _box(pts, W: int, H: int):
    """A normalised rectangle from any two corners, or None if degenerate."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = int(min(xs)), int(max(xs))
    y0, y1 = int(min(ys)), int(max(ys))
    # PIL raises on a rectangle whose corners are the wrong way round, and a
    # drag upwards and to the left is the normal way half of people draw one.
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (max(0, x0), max(0, y0), min(W, x1), min(H, y1))


def _arrow(d, start, end, rgb, w: int) -> None:
    import math as _m
    d.line([start, end], fill=rgb + (255,), width=w, joint="curve")
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = _m.hypot(dx, dy)
    if length < 4:
        return
    # The head is sized from the STROKE, not from the arrow's length: a short
    # arrow with a head scaled to its length has almost no head, and a long
    # one ends up with a head the size of a house.
    head = max(10.0, w * 5.0)
    ang = _m.atan2(dy, dx)
    for spread in (2.6, -2.6):
        d.line([end, (end[0] + head * _m.cos(ang + spread),
                      end[1] + head * _m.sin(ang + spread))],
               fill=rgb + (255,), width=w)


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
    marks = edit.get("markup") or []
    if marks:
        n = len(marks)
        redacted = sum(1 for m in marks if m.get("t") == "redact")
        # Redaction is called out separately because it is the one mark that
        # destroys pixels, and somebody reading an audit line should see that
        # it happened.
        bits.append(f"{n} mark{'' if n == 1 else 's'}"
                    + (f" ({redacted} redacted)" if redacted else ""))
    return ", ".join(bits) or "no change"
