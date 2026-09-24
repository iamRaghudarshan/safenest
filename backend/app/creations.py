"""Collages and moving highlights, made from photos the library already has.

Section 41. The point of a "creation" is that nobody asked for it: a set of
photos from one day or one album becomes a single thing worth keeping or
sending, without anybody laying it out.

WHY THERE IS NO VIDEO ENCODER HERE. A highlight reel usually means H.264,
which means FFmpeg — another large binary on a machine somebody keeps in their
house, for an output most people will look at once. An animated WebP plays in
every browser and in the phone's gallery, needs nothing installed, and is a
few hundred kilobytes. It is not a film, and it is not called one: the app
says "moving highlight".

WHY THE OUTPUT IS A PHOTO. A creation is saved back into the gallery as an
ordinary photo, not into a special table. Everything that already works —
albums, favourites, sharing by export, the trash, backup — then works on it
for free, and the alternative is a second kind of object that every screen has
to learn about.

NOTHING IS MADE WITHOUT BEING ASKED. The suggestions module offers; this one
only builds what was accepted. A gallery that quietly grows collages nobody
wanted is a chore to undo, and the person who would mind most is the person
whose library is already full.
"""
from __future__ import annotations

import io
import math

from PIL import Image, ImageOps

#: The finished collage's long edge. Big enough to look right full-screen on a
#: phone and to print small; past this the file grows faster than the picture
#: improves.
COLLAGE_MAX = 2048

#: Gap between tiles, and the mount around them, in pixels at COLLAGE_MAX.
GUTTER = 14
MARGIN = 26
BACKGROUND = (250, 249, 246)

#: How many photos a collage can hold. Past a dozen every tile is a stamp and
#: the collage stops being about any of them.
COLLAGE_MIN, COLLAGE_MAX_TILES = 2, 12

#: The moving highlight: frame size, how long each photo holds, and the cap.
REEL_SIZE = 1080
REEL_MS = 900
REEL_MIN, REEL_MAX_FRAMES = 3, 40


class CreationError(ValueError):
    """Not enough to work with, said in a sentence."""


def _grid(n: int) -> tuple[int, int]:
    """Columns and rows for n tiles, preferring a shape near square.

    Not a lookup table: the table version had a gap at 7 and 11 that nobody
    noticed until a seven-photo day produced a 1x7 strip.
    """
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = int(math.ceil(n / cols))
    return cols, rows


def _fit(im: Image.Image, w: int, h: int) -> Image.Image:
    """Fill a cell, cropping the overflow — never letterboxing.

    A collage of mixed portrait and landscape photos with bars round each one
    reads as a fault in the layout. Cropping loses edges; bars lose the whole
    idea of a grid.
    """
    return ImageOps.fit(im, (max(1, w), max(1, h)), method=Image.LANCZOS,
                        centering=(0.5, 0.45))


def _open(raw: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(raw))
    # The same transpose everything else does: a sideways portrait in a
    # collage is more obviously wrong than anywhere else in the app.
    im = ImageOps.exif_transpose(im) or im
    return im.convert("RGB") if im.mode != "RGB" else im


def collage(images: list[bytes]) -> bytes:
    """One JPEG from several photos, laid out in a near-square grid."""
    if len(images) < COLLAGE_MIN:
        raise CreationError("A collage needs at least two photos")
    images = images[:COLLAGE_MAX_TILES]

    cols, rows = _grid(len(images))
    # The canvas is sized from the grid rather than fixed, so a 2x2 collage is
    # square and a 3x2 is not — forcing everything into one aspect ratio is
    # what makes automatic layouts look automatic.
    cell = (COLLAGE_MAX - 2 * MARGIN - (cols - 1) * GUTTER) // cols
    cell = max(120, cell)
    width = MARGIN * 2 + cols * cell + (cols - 1) * GUTTER
    height = MARGIN * 2 + rows * cell + (rows - 1) * GUTTER

    canvas = Image.new("RGB", (width, height), BACKGROUND)
    for i, raw in enumerate(images):
        try:
            tile = _fit(_open(raw), cell, cell)
        except Exception:
            # One unreadable photo must not lose the other eleven.
            continue
        c, r = i % cols, i // cols
        x = MARGIN + c * (cell + GUTTER)
        y = MARGIN + r * (cell + GUTTER)
        canvas.paste(tile, (x, y))

    # The last row is usually short. Centring it stops a 2-of-3 row hanging
    # off the left edge like a mistake.
    spare = len(images) % cols
    if spare:
        row = rows - 1
        shift = ((cols - spare) * (cell + GUTTER)) // 2
        if shift:
            strip = canvas.crop((0, MARGIN + row * (cell + GUTTER),
                                 width, height - MARGIN + GUTTER))
            blank = Image.new("RGB", strip.size, BACKGROUND)
            content = strip.crop((MARGIN, 0, MARGIN + spare * (cell + GUTTER), strip.height))
            blank.paste(content, (MARGIN + shift, 0))
            canvas.paste(blank, (0, MARGIN + row * (cell + GUTTER)))

    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def reel(images: list[bytes], ms: int = REEL_MS) -> bytes:
    """An animated WebP that steps through the photos.

    Every frame is the same size, because an animation whose frames differ in
    size is not a valid animation — the first frame decides, and the rest are
    fitted to it rather than the other way round.
    """
    if len(images) < REEL_MIN:
        raise CreationError("A moving highlight needs at least three photos")
    images = images[:REEL_MAX_FRAMES]

    frames: list[Image.Image] = []
    for raw in images:
        try:
            frames.append(_fit(_open(raw), REEL_SIZE, REEL_SIZE))
        except Exception:
            continue
    if len(frames) < REEL_MIN:
        raise CreationError("Too few of those photos could be read")

    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:],
                   duration=max(200, int(ms)), loop=0, quality=72, method=4)
    return buf.getvalue()
