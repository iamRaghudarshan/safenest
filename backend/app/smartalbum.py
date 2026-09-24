"""Albums that are a saved question rather than a list of photos.

Section 42. An ordinary album is a hand-picked set: you put photos in and they
stay there. A smart album is a rule — "Alice, at the beach, in 2024" — and it
answers itself every time it is opened, so a photo taken tomorrow that matches
appears without anybody filing it.

WHY THE RULE IS JSON AND NOT COLUMNS
The set of things worth filtering by is still growing: person, label, year,
month and media kind today, place as soon as the geocoder lands. Each one as
its own column is another migration and another nullable field that most rows
never use, and the combination is what matters anyway.

WHY IT IS NOT A SQL STRING
The obvious shortcut is to store a fragment of SQL and paste it into a query.
That is an injection hole with a UI attached to it, and it would be reachable
by anyone who can name an album. The rule is a dict of known keys and every
value is used as a bound parameter.
"""
from __future__ import annotations

import json

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import dialect
from .models import GalleryPhoto, PhotoLabel, PhotoPerson

#: The only keys a rule may contain. Anything else is ignored rather than
#: rejected: a rule written by a newer version of the app should still work
#: here, minus the part this version does not understand, instead of the
#: album refusing to open.
KEYS = ("person_id", "label", "year", "month", "kind", "place", "favourite")


def parse(raw: str | None) -> dict:
    """A stored rule, as a dict. Never raises."""
    if not raw:
        return {}
    try:
        got = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if k in KEYS and v not in (None, "", [])}


def dumps(rule: dict) -> str:
    return json.dumps({k: v for k, v in (rule or {}).items()
                       if k in KEYS and v not in (None, "", [])})


def describe(rule: dict) -> str:
    """The rule in words, for the album's subtitle.

    A smart album whose rule is invisible is a folder somebody cannot reason
    about: when it shows the wrong photos there is no way to tell why.
    """
    bits = []
    if rule.get("label"):
        bits.append(str(rule["label"]))
    if rule.get("place"):
        bits.append("in " + str(rule["place"]))
    # Both kinds, not just video. Naming only one of them meant a rule of
    # {"kind": "photo"} described itself as "everything" — which is the exact
    # failure this function exists to prevent: an album that fills itself for
    # a reason the screen does not show.
    if rule.get("kind") == "video":
        bits.append("videos")
    elif rule.get("kind") == "photo":
        bits.append("photos")
    if rule.get("favourite"):
        bits.append("favourites")
    if rule.get("month"):
        months = ("", "January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December")
        try:
            bits.append(months[int(rule["month"])])
        except (ValueError, IndexError):
            pass
    if rule.get("year"):
        bits.append(str(rule["year"]))
    if rule.get("person_id"):
        bits.append("one person")
    return ", ".join(bits) or "everything"


def apply(sel, db: Session, user_id: int, rule: dict):
    """Narrow a gallery query by a rule. Every value is a bound parameter."""
    if not rule:
        return sel

    if rule.get("person_id"):
        sel = sel.filter(GalleryPhoto.id.in_(
            db.query(PhotoPerson.photo_id).filter(
                PhotoPerson.person_id == int(rule["person_id"]))))

    if rule.get("label"):
        sel = sel.filter(GalleryPhoto.id.in_(
            db.query(PhotoLabel.photo_id).filter(
                PhotoLabel.user_id == user_id,
                PhotoLabel.label == str(rule["label"])[:40].lower())))

    if rule.get("year"):
        try:
            sel = sel.filter(dialect.year_of(GalleryPhoto.taken_at)
                             == int(rule["year"]))
        except (TypeError, ValueError):
            pass

    if rule.get("month"):
        try:
            sel = sel.filter(dialect.month_of(GalleryPhoto.taken_at)
                             == int(rule["month"]))
        except (TypeError, ValueError):
            pass

    if rule.get("kind"):
        # The SAME test the gallery's own kind filter uses, not a fresh one.
        # `kind` is NULL for photos and only written for videos — _present
        # defaults it to "photo" on the way out — so `== "photo"` matches no
        # row at all. A saved search for photos came back empty on a library
        # full of them, and the album still said "0 photos" with a straight
        # face.
        k = str(rule["kind"])[:8].lower()
        if k == "video":
            sel = sel.filter(GalleryPhoto.kind == "video")
        elif k == "photo":
            sel = sel.filter(or_(GalleryPhoto.kind.is_(None),
                                 GalleryPhoto.kind != "video"))

    if rule.get("place"):
        sel = sel.filter(GalleryPhoto.place == str(rule["place"])[:120])

    if rule.get("favourite"):
        sel = sel.filter(GalleryPhoto.is_favorite == 1)

    return sel
