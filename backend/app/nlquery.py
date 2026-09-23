"""Turn "Alice at the beach in 2024" into filters.

Section 26. The pieces this needs all exist separately — people, labels,
dates, and CLIP semantic search — and the only thing missing was reading a
sentence and deciding which of them a word belongs to.

WHAT IT IS
A parser, not a language model. It matches words against things this library
actually contains: the names of people the owner has, the label vocabulary the
indexer uses, and dates. Everything it cannot place is left alone and handed
to semantic search, which is what CLIP is for.

WHY THAT WAY ROUND
A model that invented filters would produce confident nonsense on a query it
misread — "photos of Sam" narrowing to a person called Sam who does not exist
returns nothing, and the person is told their photos are missing. Matching
against what exists means a word is only ever turned into a filter when the
filter can actually be satisfied.

WHAT IT DELIBERATELY DOES NOT DO
Guess. An unrecognised word is not dropped and not forced into a filter; it
stays in the free text. A query that matches nothing structured is simply a
semantic search, which is exactly what it was before.
"""
from __future__ import annotations

import re
from datetime import date

#: Words that carry no meaning for a photo search. Removing them stops "of"
#: and "the" being handed to CLIP as though they described a picture.
_STOP = {
    "a", "an", "the", "of", "in", "on", "at", "with", "and", "from", "to",
    "my", "me", "our", "photos", "photo", "pictures", "picture", "pics",
    "pic", "show", "find", "search", "all", "some", "for", "taken", "was",
    "were", "is", "are", "that", "this", "it", "im", "i",
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_KIND_WORDS = {"video": "video", "videos": "video", "clip": "video",
               "clips": "video", "movie": "video", "movies": "video"}

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def parse(query: str, people: list[tuple[int, str]], labels: list[str],
          today: date | None = None) -> dict:
    """Split a query into filters plus whatever is left over.

    `people` is [(id, name)] for THIS user and `labels` the label vocabulary,
    so matching is always against things that exist. Returns person_id,
    label, year, month, kind and the residual `text`.
    """
    today = today or date.today()
    raw = _norm(query)
    if not raw:
        return {"person_id": None, "person_name": None, "label": None,
                "year": None, "month": None, "kind": None, "text": "",
                "matched": []}

    words = raw.split()
    used = [False] * len(words)
    matched: list[str] = []

    def claim(i: int, n: int = 1):
        for k in range(i, min(i + n, len(words))):
            used[k] = True

    # --- people, longest name first ----------------------------------------
    # "Anna Maria" must win over "Anna" when both exist, or the more specific
    # person is unreachable by name.
    person_id = person_name = None
    for pid, name in sorted(people, key=lambda p: -len(_norm(p[1] or ""))):
        n = _norm(name)
        if not n:
            continue
        parts = n.split()
        for i in range(len(words) - len(parts) + 1):
            if words[i:i + len(parts)] == parts and not any(used[i:i + len(parts)]):
                person_id, person_name = pid, name
                claim(i, len(parts))
                matched.append("person:" + name)
                break
        if person_id:
            break

    # --- label, longest first so "group of people" beats "people" ----------
    label = None
    for lab in sorted(labels, key=len, reverse=True):
        parts = _norm(lab).split()
        if not parts:
            continue
        for i in range(len(words) - len(parts) + 1):
            if words[i:i + len(parts)] == parts and not any(used[i:i + len(parts)]):
                label = lab
                claim(i, len(parts))
                matched.append("label:" + lab)
                break
        if label:
            break

    # --- dates --------------------------------------------------------------
    year = month = None
    for i, w in enumerate(words):
        if used[i]:
            continue
        if _YEAR.fullmatch(w):
            year = int(w); claim(i); matched.append("year:" + w); break
    for i, w in enumerate(words):
        if used[i]:
            continue
        if w in _MONTHS:
            month = _MONTHS[w]; claim(i); matched.append("month:" + w); break

    # Relative dates, resolved against today so "last year" means a year.
    for phrase, y in (("last year", today.year - 1), ("this year", today.year)):
        parts = phrase.split()
        for i in range(len(words) - 1):
            if words[i:i + 2] == parts and not any(used[i:i + 2]):
                if year is None:
                    year = y
                    matched.append("year:%d" % y)
                claim(i, 2)
                break

    # --- media kind ---------------------------------------------------------
    kind = None
    for i, w in enumerate(words):
        if used[i]:
            continue
        if w in _KIND_WORDS:
            kind = _KIND_WORDS[w]; claim(i); matched.append("kind:" + kind); break

    leftover = [w for i, w in enumerate(words)
                if not used[i] and w not in _STOP]
    return {"person_id": person_id, "person_name": person_name,
            "label": label, "year": year, "month": month, "kind": kind,
            "text": " ".join(leftover), "matched": matched}
