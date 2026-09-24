"""Things the library implies, offered rather than done.

Sections 39, 40 and the "automatic" half of 41. The app can see that eleven
photos were taken on one afternoon, that forty documents are sitting loose at
the top level, that nine photos are near-duplicates of each other. Acting on
any of that without asking produces a library somebody has to undo, and the
person who minds most is the one whose library is already large.

So nothing here changes anything. Each suggestion is a proposal with a stable
KEY, and the key is what makes "no" mean no: a dismissal is stored against it,
and the same proposal is never offered again. Without that, "not now" becomes
"ask me every time I open the app", which is how people learn to ignore a
panel entirely.

WHY THE KEY IS DERIVED, NOT STORED. A suggestion is recomputed from the
library each time it is asked for — there is no queue of pending proposals to
keep in step with reality. That means the key has to fall out of the thing
itself ("collage:2026-05-01") rather than being handed out, or a dismissal
would attach to a row that no longer exists and the proposal would come back
wearing a new number.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import ist
from .models import Document, GalleryPhoto, Suggestion

#: A day needs at least this many photos before a collage is worth offering.
#: Three is a coincidence; six is an afternoon.
COLLAGE_MIN_PHOTOS = 6

#: And a moving highlight wants more than a collage, because its whole appeal
#: is that there is too much to lay out at once.
REEL_MIN_PHOTOS = 10

#: Loose documents at the top level before "shall I file these?" is worth
#: saying. Below this it is faster to drag them than to read the offer.
FILE_MIN_DOCS = 8

#: How many suggestions to return. A list nobody reaches the end of is a list
#: nobody reads the start of either.
MAX_SUGGESTIONS = 8


def _dismissed(db: Session, user_id: int) -> set[str]:
    return {k for (k,) in db.query(Suggestion.key)
            .filter(Suggestion.user_id == user_id).all()}


def _days_with_photos(db: Session, user_id: int, minimum: int):
    """Days holding at least `minimum` live, unarchived photos, newest first."""
    rows = (db.query(GalleryPhoto.taken_at, func.count(GalleryPhoto.id))
            .filter(GalleryPhoto.user_id == user_id,
                    GalleryPhoto.is_trashed == 0,
                    (GalleryPhoto.is_archived == 0)
                    | (GalleryPhoto.is_archived.is_(None)),
                    GalleryPhoto.taken_at.isnot(None),
                    # A creation of a creation is a hall of mirrors: anything
                    # this module made is excluded from what it looks at.
                    (GalleryPhoto.caption.is_(None))
                    | (~GalleryPhoto.caption.like("Collage %")))
            .group_by(GalleryPhoto.taken_at)
            .having(func.count(GalleryPhoto.id) >= minimum)
            .order_by(GalleryPhoto.taken_at.desc())
            .limit(40).all())
    return [(d, int(n)) for d, n in rows if d]


def _day_words(day) -> str:
    """A date a person would say out loud: "1 May 2026".

    Not strftime("%-d %B %Y"). The dash flag that strips a leading zero is a
    glibc extension: it raises ValueError on Windows, which is where this
    runs, and it would have taken the whole suggestions panel down on the one
    platform it ships to.
    """
    try:
        return "%d %s %d" % (day.day, day.strftime("%B"), day.year)
    except Exception:
        return str(day)


def _photo_ids(db: Session, user_id: int, day, limit: int) -> list[int]:
    rows = (db.query(GalleryPhoto.id)
            .filter(GalleryPhoto.user_id == user_id,
                    GalleryPhoto.is_trashed == 0,
                    GalleryPhoto.taken_at == day)
            .order_by(GalleryPhoto.id.asc()).limit(limit).all())
    return [r[0] for r in rows]


def build(db: Session, user_id: int) -> list[dict]:
    """Everything worth offering right now, best first."""
    skip = _dismissed(db, user_id)
    out: list[dict] = []

    # ---- a collage, or a moving highlight, from a busy day -----------------
    for day, n in _days_with_photos(db, user_id, COLLAGE_MIN_PHOTOS):
        kind = "reel" if n >= REEL_MIN_PHOTOS else "collage"
        key = f"{kind}:{day.isoformat()}"
        if key in skip:
            continue
        out.append({
            "key": key,
            "kind": kind,
            "title": ("A moving highlight from " if kind == "reel"
                      else "A collage from ") + _day_words(day),
            "detail": f"{n} photos that day",
            "day": day.isoformat(),
            "photo_ids": _photo_ids(db, user_id, day, 40 if kind == "reel" else 12),
        })
        if len(out) >= MAX_SUGGESTIONS:
            return out

    # ---- loose documents --------------------------------------------------
    loose = (db.query(func.count(Document.id))
             .filter(Document.user_id == user_id, Document.is_trashed == 0,
                     Document.folder_id.is_(None)).scalar() or 0)
    if loose >= FILE_MIN_DOCS and "file-loose" not in skip:
        # By KIND, because that is the grouping the classifier already made
        # and the one a person would have used anyway.
        by_kind = (db.query(Document.kind, func.count(Document.id))
                   .filter(Document.user_id == user_id, Document.is_trashed == 0,
                           Document.folder_id.is_(None),
                           Document.kind.isnot(None))
                   .group_by(Document.kind)
                   .order_by(func.count(Document.id).desc()).limit(4).all())
        groups = [{"kind": k, "count": int(n)} for k, n in by_kind if k and n >= 3]
        if groups:
            out.append({
                "key": "file-loose",
                "kind": "file_documents",
                "title": "File loose documents into folders",
                "detail": f"{loose} are sitting at the top level",
                "groups": groups,
            })

    return out[:MAX_SUGGESTIONS]


def dismiss(db: Session, user_id: int, key: str) -> None:
    """Remember a no. Idempotent — saying no twice is still no."""
    key = (key or "").strip()[:120]
    if not key:
        return
    if db.query(Suggestion).filter(Suggestion.user_id == user_id,
                                   Suggestion.key == key).first():
        return
    db.add(Suggestion(user_id=user_id, key=key, created_at=ist.now()))
    db.commit()
