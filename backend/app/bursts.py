"""Photos taken in a rush, grouped so the timeline is not twelve of the same thing.

Section 11. Holding the shutter gives you eight nearly identical frames, and a
timeline that shows all eight is mostly noise: the interesting content of that
moment is one picture, and the other seven are there in case it blinked.

WHAT COUNTS AS A BURST
Taken close together in time, AND visually similar. Time alone is wrong — a
wedding produces a hundred photos a minute that are all different, and
collapsing them would hide the event. Similarity alone is wrong too: the same
view photographed in March and again in June is not a burst.

The similarity test reuses the perceptual hash the duplicate finder already
computes, so this costs no new processing.

WHAT IT DELIBERATELY DOES NOT DO
Delete, move, or hide anything. A burst is a presentation decision — show one
tile with a count, open it to see all of them — and the photos stay exactly
where they are. Somebody who wants all eight in the grid should be able to
turn it off and see all eight.
"""
from __future__ import annotations

#: Frames more than this far apart are not one burst, however alike they look.
#: Ten seconds covers a held shutter and a few quick taps; a minute would
#: start swallowing genuinely different photos of the same scene.
WINDOW_SECONDS = 10

#: How alike two frames must be, as Hamming distance over the 64-bit dHash.
#: The duplicate finder calls anything under 8 a near-duplicate; a burst is
#: tighter than that, because these are meant to be the SAME shot.
MAX_DISTANCE = 6

#: Below this, leave them alone. Two photos is not a burst, it is two photos,
#: and hiding one of a pair behind a badge helps nobody.
MIN_GROUP = 3


def _bits(h: str | None) -> int | None:
    if not h:
        return None
    try:
        return int(h, 16)
    except ValueError:
        return None


def distance(a: str | None, b: str | None) -> int | None:
    """Hamming distance between two dHashes, or None if either is missing."""
    x, y = _bits(a), _bits(b)
    if x is None or y is None:
        return None
    return bin(x ^ y).count("1")


def group(photos: list) -> list[list]:
    """Split a time-ordered run of photos into bursts.

    Takes whatever has `shot_at` (or `created_at`) and `phash`, and returns
    lists — one per burst — in the order they were given. Photos that belong
    to no burst come back as groups of one, so the caller can render the whole
    timeline from this without reassembling anything.
    """
    out: list[list] = []
    run: list = []

    def flush():
        if not run:
            return
        # A run that never reached MIN_GROUP is not a burst; give each photo
        # back on its own rather than badging a pair.
        if len(run) >= MIN_GROUP:
            out.append(list(run))
        else:
            out.extend([p] for p in run)
        run.clear()

    for p in photos:
        when = getattr(p, "shot_at", None) or getattr(p, "created_at", None)
        if when is None:
            flush()
            out.append([p])
            continue

        if not run:
            run.append(p)
            continue

        prev = run[-1]
        prev_when = getattr(prev, "shot_at", None) or getattr(prev, "created_at", None)
        gap = abs((when - prev_when).total_seconds()) if prev_when else None
        d = distance(getattr(p, "phash", None), getattr(prev, "phash", None))

        # Both tests, not either. See the note at the top of the file.
        if gap is not None and gap <= WINDOW_SECONDS and d is not None and d <= MAX_DISTANCE:
            run.append(p)
        else:
            flush()
            run.append(p)

    flush()
    return out
