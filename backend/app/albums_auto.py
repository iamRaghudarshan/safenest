"""Albums nobody had to make.

Every photo already has a CLIP vector, written for search-by-content. Those
vectors also say which pictures resemble each other, so the grouping is sitting
in the database already — it just was not being read.

Two signals, because either alone is wrong:

  content   pictures of the same kind of thing cluster together
  time      pictures taken in the same few days belong to the same occasion

Content alone merges every beach photo you have ever taken into one album across
nine years. Time alone puts the wedding and the parking-receipt you snapped that
afternoon in the same place. Requiring both is what makes a group feel like an
event someone actually remembers.

No new model and no download: the vectors exist, and the naming reuses the CLIP
text encoder that already ships for search.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

import numpy as np

from . import vision
from .models import GalleryPhoto, PhotoVector

# Cosine similarity above which two photos are "the same sort of picture". 0.75
# was chosen against this library: lower and unrelated indoor shots merge, higher
# and obvious sets of the same scene split apart.
SIMILAR = 0.75
# Photos more than this far apart are separate occasions even if they look alike.
SAME_TRIP_DAYS = 3
MIN_ALBUM = 4          # three photos is not an event worth its own album
MAX_ALBUMS = 24

# Candidate names, scored against each group's average vector. Deliberately
# concrete: "a photo of a birthday cake" scores far more reliably than "party".
LABELS = [
    ("Food", "a photo of a meal, food on a plate, or a restaurant dish"),
    ("Travel", "a photo of a landscape, mountains, a beach or a tourist place"),
    ("Family", "a photo of a family gathering with several people together"),
    ("Celebrations", "a photo of a birthday cake, a party or a festival"),
    ("Documents", "a scanned document, a bill, a receipt or a paper form"),
    ("Screenshots", "a screenshot of a phone or computer screen"),
    ("Shopping", "a photo of shopping, a shop, products or price tags"),
    ("Vehicles", "a photo of a car, a bike or a vehicle"),
    ("Pets", "a photo of a pet, a dog or a cat"),
    ("Nature", "a photo of plants, flowers, trees or a garden"),
    ("Home", "a photo taken inside a house, a room or furniture"),
    ("Work", "a photo of an office, a whiteboard, a meeting or a desk"),
]


def _load(db, user_id: int):
    """Every live photo of one user that has a vector, newest first."""
    rows = (db.query(GalleryPhoto, PhotoVector)
            .join(PhotoVector, PhotoVector.photo_id == GalleryPhoto.id)
            .filter(GalleryPhoto.user_id == user_id, GalleryPhoto.is_trashed == 0)
            .order_by(GalleryPhoto.taken_at.desc(), GalleryPhoto.id.desc())
            .all())
    photos, vectors = [], []
    for photo, vec in rows:
        arr = vision.unpack(vec.vec)
        # A vector of the wrong length is from an older model; skipping beats
        # crashing the whole grouping over one stale row.
        if arr is None or arr.shape[0] != vision.CLIP_DIM:
            continue
        photos.append(photo)
        vectors.append(arr)
    if not photos:
        return [], None
    matrix = np.vstack(vectors).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return photos, matrix / norms


def _when(photo):
    return photo.taken_at or photo.shot_at or photo.created_at


def _group(photos, matrix) -> list[list[int]]:
    """Greedy clustering on content, then split each cluster by time.

    Greedy rather than k-means: the number of events is unknown, and k-means
    would need it chosen in advance. This walks the photos newest-first, starts a
    group whenever one matches nothing so far, and is O(n·groups) — fast enough
    for a few thousand photos on a laptop.
    """
    seeds: list[int] = []
    members: dict[int, list[int]] = defaultdict(list)
    for i in range(len(photos)):
        if seeds:
            sims = matrix[seeds] @ matrix[i]
            best = int(np.argmax(sims))
            if float(sims[best]) >= SIMILAR:
                members[seeds[best]].append(i)
                continue
        seeds.append(i)
        members[i].append(i)

    # Now split on time: the same kind of picture from two different years is two
    # different occasions, however alike the frames are.
    out: list[list[int]] = []
    for seed in seeds:
        idx = sorted(members[seed], key=lambda j: (_when(photos[j]) or photos[j].created_at))
        run: list[int] = []
        previous = None
        for j in idx:
            stamp = _when(photos[j])
            if previous is not None and stamp is not None and previous is not None:
                gap = abs((stamp - previous).days)
                if gap > SAME_TRIP_DAYS:
                    out.append(run)
                    run = []
            run.append(j)
            previous = stamp or previous
        if run:
            out.append(run)
    return [g for g in out if len(g) >= MIN_ALBUM]


def _name(mean_vec) -> tuple[str, float]:
    """Best-matching label for a group's average look, zero-shot."""
    try:
        prompts = [text for _, text in LABELS]
        bank = np.vstack([vision.embed_text(p) for p in prompts]).astype(np.float32)
        bank /= np.linalg.norm(bank, axis=1, keepdims=True)
        scores = bank @ mean_vec
        best = int(np.argmax(scores))
        return LABELS[best][0], float(scores[best])
    except Exception:
        return "Photos", 0.0


def suggest(db, user_id: int, limit: int = MAX_ALBUMS) -> list[dict]:
    """Album suggestions for one user. Pure read — nothing is created."""
    photos, matrix = _load(db, user_id)
    if not photos:
        return []
    groups = _group(photos, matrix)
    if not groups:
        return []

    out = []
    for members in groups:
        mean = matrix[members].mean(axis=0)
        norm = np.linalg.norm(mean)
        mean = mean / norm if norm else mean
        label, score = _name(mean)
        when = [_when(photos[j]) for j in members]
        when = sorted([w for w in when if w])
        span = ""
        if when:
            first, last = when[0], when[-1]
            span = (first.strftime("%b %Y") if first.year == last.year
                    and first.month == last.month
                    else f"{first.strftime('%b %Y')} – {last.strftime('%b %Y')}")
        out.append({
            "name": f"{label} · {span}" if span else label,
            "label": label,
            "confidence": round(score, 3),
            "span": span,
            "count": len(members),
            "photo_ids": [photos[j].id for j in members],
            "cover_id": photos[members[0]].id,
        })
    # Biggest first — a 40-photo trip is more worth offering than a 4-photo one.
    out.sort(key=lambda a: -a["count"])
    return out[:limit]
