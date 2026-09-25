"""Choosing the one face that represents a person.

WHAT WAS WRONG. The People page picked the BIGGEST face somebody had. Size is
necessary — a 24-pixel face cannot be recognised however good it is — but it is
nowhere near sufficient, and on a real library it produced portraits that were
half-profiles, motion-blurred, or a circle with two people crammed into it.
Measured on the owner's library, four of the first twelve were turned away or
soft. The complaint was exactly right: "I want neat and clear faces."

So four things are measured, and a face has to do well at all of them:

  SIZE        pixels across, in the original. Nothing invents detail later.
  SHARPNESS   variance of the Laplacian. A blurred face has little.
  FRONTALITY  where the nose sits between the eyes. Square-on or turned away.
  SOLITUDE    whether anybody else lands inside the crop. A circle with two
              people in it names neither of them. Measured twice over: from
              the recorded rectangles, which is free, and again from what the
              detector finds in the finished crop — because a face too small
              or too turned to have been recorded still ruins the portrait it
              appears in.

Multiplied rather than added, because these are not interchangeable: a huge
sharp profile is still a profile, and adding scores lets one strength hide a
disqualifying weakness. Every term keeps a floor above zero so that a person
whose every photograph is poor still gets their best one rather than nothing.

THE EXPENSIVE PART IS DECODING. Sharpness and frontality need the actual
pixels, and originals here are twelve megapixels. So the cheap, pure measures
shortlist first and only a handful of candidates are ever opened, and the
answer is remembered on the person so it is paid for once.
"""
from __future__ import annotations

import os

from . import storage

#: How many candidates are opened and measured properly. Six is enough to find
#: a good face when one exists — beyond it the extra decodes cost more than the
#: marginal improvement, and people with hundreds of faces are exactly the ones
#: where a good face turns up early.
SHORTLIST = 6

#: Below this a face is too small to be a portrait whatever else is right.
MIN_PX = 60

#: Laplacian variance at which a face counts as fully sharp. Chosen from the
#: owner's own library, where clearly-focused faces measured 400-1400 and the
#: soft ones 170-220.
SHARP_FULL = 400.0

#: How much of ANOTHER face has to fall inside the crop before it is a
#: problem. A sliver of a shoulder-mate is unavoidable in a group shot; a
#: quarter of their face means the circle has two people in it.
INTRUDE = 0.25


#: What a face has to clear to be shown as somebody's portrait at all.
#:
#: These are not guesses. Every portrait on the owner's library was rendered
#: and labelled by eye as a usable face or not, then the measurements of both
#: groups were compared. These three thresholds separate them exactly — all
#: seventeen usable faces pass and all six failures are caught: a temple
#: carving, a hand across a face, a full profile, and three motion-blurred
#: crowd faces.
#:
#:   SCORE       the detector's own confidence. A stone carving scored 0.823
#:               and a blurred crowd face 0.841, where real faces sat at 0.92
#:               and up. The bar is 0.90 rather than 0.92 on purpose: a child
#:               at 0.917 and an elderly man at 0.916 are perfectly good
#:               portraits, and a bar tight enough to exclude them hid two
#:               real people, one of them in seven photographs. Everything
#:               that deserves rejecting between 0.90 and 0.92 is caught by
#:               being turned away or blurred instead.
#:   FRONTALITY  0.47 was a hand over a face, 0.00 a full profile. A genuine
#:               face at a slight angle measured 0.54 and is fine.
#:   SHARPNESS   good faces measured 314 and up. The rejects were 16, 159, 175.
PROPER_SCORE = 0.90
PROPER_FRONT = 0.50
PROPER_SHARP = 300.0


def is_proper(score: float | None, front: float, sharpness: float) -> bool:
    """Is this a face worth showing as a person, rather than a hand or a
    half-turned head?

    A person whose best face fails this is not deleted or ungrouped — their
    photos are all still there and still searchable. They are simply kept out
    of the default People view, because a grid where a wristwatch and a stone
    carving sit beside somebody's mother is a grid nobody can use.
    """
    return (float(score or 0.0) >= PROPER_SCORE
            and front >= PROPER_FRONT
            and sharpness >= PROPER_SHARP)


def parse_bbox(bbox: str | None):
    """(x, y, w, h) as floats, or None."""
    try:
        x, y, w, h = (float(v) for v in (bbox or "").split(","))
    except (ValueError, AttributeError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def crop_square(box, pad: float):
    """The square the crop will take, around a detector rectangle."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    half = max(w, h) * (1 + pad) / 2
    return cx - half, cy - half, cx + half, cy + half


def _intersection(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    return ix * iy


def solitude(box, others, pad: float) -> float:
    """1.0 when the crop holds only this face, lower as others intrude.

    Pure arithmetic on rectangles, so it runs over every candidate before
    anything is decoded — which is the point: it is the one quality measure
    that costs nothing, and it removes the worst offenders first.
    """
    square = crop_square(box, pad)
    worst = 0.0
    for other in others:
        area = other[2] * other[3]
        if area <= 0:
            continue
        got = _intersection(square, (other[0], other[1],
                                     other[0] + other[2], other[1] + other[3]))
        worst = max(worst, got / area)
    if worst <= INTRUDE:
        return 1.0
    # Never zero: a person photographed only ever in a crowd still needs a
    # face, and refusing to choose would leave them with none.
    return max(0.25, 1.0 - worst)


def size_score(px: float) -> float:
    """Diminishing returns: past a few hundred pixels, bigger stops helping."""
    if px <= 0:
        return 0.0
    return min(1.0, px / 240.0)


def sharp_score(var: float) -> float:
    return max(0.05, min(1.0, var / SHARP_FULL))


def rank(px: float, sharpness: float, front: float, alone: float,
         score: float | None, faces_in_crop: int = 1) -> float:
    """One number for how good a portrait a face makes.

    Multiplicative on purpose. A huge sharp half-profile is still a
    half-profile, and a sum would let its size carry it past a smaller face
    looking straight at the camera — which is the exact failure being fixed.
    """
    conf = 0.6 + 0.4 * float(score if score is not None else 0.8)
    # A crop the detector finds TWO faces in has two people in the circle,
    # whatever the rectangles said. `solitude` can only see faces that were
    # recorded; this catches the ones that never were - a face too small or
    # too turned to be stored still ruins the portrait it appears in.
    crowd = 1.0 if faces_in_crop <= 1 else max(0.3, 1.0 / faces_in_crop)
    # Frontality keeps a floor: on some faces the detector finds no landmarks
    # at all in a tight crop, and that must rank low without being an
    # automatic disqualification.
    return (size_score(px) * sharp_score(sharpness)
            * max(0.15, front) * alone * conf * crowd)


def measure(bgr, box, pad: float, vision) -> tuple[float, float, int]:
    """(sharpness, frontality, faces found) for one face, from the pixels."""
    import cv2

    ih, iw = bgr.shape[:2]
    x0, y0, x1, y1 = crop_square(box, pad)
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(iw, int(x1)), min(ih, int(y1))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return 0.0, 0.0, 0
    crop = bgr[y0:y1, x0:x1]
    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(grey, cv2.CV_64F).var())

    # Re-detect inside the crop for the landmarks. Running the detector on a
    # small square is cheap next to the decode that has already happened, and
    # a face it cannot find in a tight crop of itself is a poor portrait for
    # the same reasons it is hard to detect.
    front = 0.0
    found = 0
    try:
        got = vision.detect_faces(crop)
        found = len(got)
        for g in got:
            front = max(front, vision.frontality(g.get("landmarks")))
    except Exception:
        front, found = 0.0, 0
    return sharpness, front, found


def choose(faces, photos, user_id: int, pad: float, vision,
           shortlist: int = SHORTLIST):
    """The best portrait among a person's faces, measured properly.

    `faces` are PhotoFace rows; `photos` maps photo_id to a GalleryPhoto.
    Returns (chosen row, is it a proper face), or (None, False) when none of
    them can be used at all.
    """
    import cv2

    usable = []
    for f in faces:
        box = parse_bbox(getattr(f, "bbox", None))
        if box and max(box[2], box[3]) >= MIN_PX:
            usable.append((f, box))
    if not usable:
        # Nothing clears the size bar. Fall back to the largest there is: a
        # poor portrait still beats a blank circle.
        sized = [(f, parse_bbox(getattr(f, "bbox", None))) for f in faces]
        sized = [(f, b) for f, b in sized if b]
        if not sized:
            return None, False
        return max(sized, key=lambda t: max(t[1][2], t[1][3]))[0], False

    # Who else is in each photograph, so solitude can be measured without
    # opening anything.
    others = {}
    for f, box in usable:
        others.setdefault(f.photo_id, []).append(box)

    scored = []
    for f, box in usable:
        rest = [b for b in others.get(f.photo_id, []) if b is not box]
        alone = solitude(box, rest, pad)
        px = max(box[2], box[3])
        # The cheap estimate decides who gets opened.
        scored.append((size_score(px) * alone, f, box, alone, px))
    scored.sort(key=lambda t: -t[0])

    best = None
    best_rank = -1.0
    best_proper = False
    for _, f, box, alone, px in scored[:shortlist]:
        photo = photos.get(f.photo_id)
        if not photo:
            continue
        path = storage.media_path(storage.GALLERY, user_id,
                                  storage.ORIGINAL, photo.filename)
        if not os.path.isfile(path):
            continue
        bgr = cv2.imread(path)
        if bgr is None:
            continue
        sharpness, front, found = measure(bgr, box, pad, vision)
        r = rank(px, sharpness, front, alone, getattr(f, "score", None), found)
        if r > best_rank:
            best_rank, best = r, f
            best_proper = is_proper(getattr(f, "score", None), front, sharpness)

    # Every candidate failed to open. Fall back to the cheap ranking rather
    # than showing nobody — and say it is not a proper portrait, because
    # nothing was ever measured.
    if best is None:
        return scored[0][1], False
    return best, best_proper
