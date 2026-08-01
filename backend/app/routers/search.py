"""One search box for the whole app.

Records live in eight modules and photos live in a ninth, so "when did I pay the
electricity bill" used to mean guessing which screen it was filed under and
searching there. This looks everywhere at once.

Deliberately NOT an embedding model over the records. Semantic search earns its
keep when there is a large corpus of prose; a personal finance database is a
small set of short, highly structured rows, where matching the words and reading
the obvious intent — a year, an amount, a module name — beats vector similarity
and needs no extra 90 MB download. Photos are the exception: they have no words,
so they go through CLIP, which is already here.
"""
import re
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import ist, storage, vision
from ..database import get_db
from ..models import (CreditCard, Document, Expense, GalleryPhoto, Insurance,
                      Investment, Loan, Reminder, Todo, User, VaultItem)
from ..security import get_current_user
from .gallery import media_url

router = APIRouter(prefix="/api/search", tags=["search"])

PER_KIND = 6

# Words that name a module, so "show my loans" filters rather than full-texting
# the word "loans" across everything.
MODULE_WORDS = {
    "expense": "expenses", "expenses": "expenses", "spend": "expenses",
    "spent": "expenses", "bill": "expenses", "bills": "expenses",
    "loan": "loans", "loans": "loans", "emi": "loans",
    "card": "cards", "cards": "cards", "credit": "cards",
    "insurance": "insurance", "policy": "insurance", "policies": "insurance",
    "investment": "investments", "investments": "investments", "sip": "investments",
    "reminder": "reminders", "reminders": "reminders", "due": "reminders",
    "todo": "todos", "todos": "todos", "task": "todos", "tasks": "todos",
    "document": "documents", "documents": "documents", "doc": "documents",
    "docs": "documents", "scan": "documents",
    "photo": "photos", "photos": "photos", "picture": "photos",
    "pictures": "photos", "image": "photos", "images": "photos",
    "password": "vault", "passwords": "vault", "vault": "vault",
}

_YEAR = re.compile(r"\b(20\d{2})\b")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse(query: str) -> dict:
    """Read the obvious structure out of a question before matching words.

    "electricity bills last year" carries a module and a time range that a plain
    LIKE would treat as three more search words.
    """
    text = (query or "").strip()
    low = text.lower()
    out = {"text": text, "modules": set(), "year": None, "month": None, "terms": []}

    for word in re.findall(r"[a-z]+", low):
        if word in MODULE_WORDS:
            out["modules"].add(MODULE_WORDS[word])
        if word[:3] in _MONTHS and len(word) >= 3:
            out["month"] = _MONTHS[word[:3]]

    year = _YEAR.search(low)
    if year:
        out["year"] = int(year.group(1))
    elif "last year" in low:
        out["year"] = ist.today().year - 1
    elif "this year" in low:
        out["year"] = ist.today().year

    # What is left after the structural words is what to actually match on.
    drop = set(MODULE_WORDS) | {"last", "this", "year", "month", "my", "the",
                                "show", "find", "all", "of", "in", "for", "me"}
    out["terms"] = [w for w in re.findall(r"[a-z0-9@./-]{2,}", low)
                    if w not in drop and not w.isdigit()]
    return out


def _in_year(rows, field, year, month):
    """Filter already-fetched rows by a date field, tolerating None."""
    if not year and not month:
        return rows
    keep = []
    for r in rows:
        value = getattr(r, field, None)
        if not value:
            continue
        if year and value.year != year:
            continue
        if month and value.month != month:
            continue
        keep.append(r)
    return keep


@router.get("")
def search(q: str = "", limit: int = PER_KIND,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything matching, grouped by what kind of thing it is."""
    intent = parse(q)
    text = intent["text"]
    if len(text) < 2:
        return {"query": text, "groups": [], "total": 0, "understood": {}}

    wanted = intent["modules"]
    limit = max(1, min(int(limit or PER_KIND), 25))
    groups = []

    # "show my cards" names a module and nothing else. Matching the literal words
    # "show my cards" against every column finds nothing, which is why that query
    # returned empty while four cards sat in the table. With no search terms left
    # after the structural ones, the answer is simply that module's contents.
    browsing = bool(wanted) and not intent["terms"]
    like = "%%" if browsing else f"%{text}%"

    def add(kind: str, label: str, rows, present):
        if not rows:
            return
        groups.append({"kind": kind, "label": label, "count": len(rows),
                       "items": [present(r) for r in rows[:limit]]})

    def wants(kind: str) -> bool:
        return not wanted or kind in wanted

    uid = user.id
    if wants("expenses"):
        rows = (db.query(Expense).filter(Expense.user_id == uid)
                .filter((Expense.note.like(like)) | (Expense.category.like(like))
                        | (Expense.method.like(like)))
                .order_by(Expense.txn_date.desc()).limit(60).all())
        rows = _in_year(rows, "txn_date", intent["year"], intent["month"])
        add("expenses", "Expenses", rows, lambda r: {
            "id": r.id, "title": r.category or "Expense",
            "sub": r.note or "", "amount": float(r.amount or 0),
            "when": ist.fmt(r.txn_date, with_time=False), "route": "expenses"})

    if wants("documents"):
        rows = (db.query(Document).filter(Document.user_id == uid, Document.is_trashed == 0)
                .filter((Document.title.like(like)) | (Document.doc_number.like(like))
                        | (Document.notes.like(like)) | (Document.ocr_text.like(like)))
                .order_by(Document.created_at.desc()).limit(60).all())
        add("documents", "Documents", rows, lambda r: {
            "id": r.id, "title": r.title, "sub": r.category or "",
            "when": ist.fmt(r.expiry_date, with_time=False), "route": "documents",
            # Says WHY it matched when the words are only inside the scan.
            "inside": bool(r.ocr_text and text.lower() in (r.ocr_text or "").lower()
                           and text.lower() not in (r.title or "").lower())})

    if wants("reminders"):
        rows = (db.query(Reminder).filter(Reminder.user_id == uid)
                .filter(Reminder.title.like(like))
                .order_by(Reminder.due_date.desc()).limit(60).all())
        add("reminders", "Reminders", rows, lambda r: {
            "id": r.id, "title": r.title, "sub": r.module_ref or "",
            "when": ist.fmt(r.due_date, with_time=False), "route": "reminders"})

    if wants("loans"):
        rows = (db.query(Loan).filter(Loan.user_id == uid)
                .filter((Loan.lender.like(like)) | (Loan.loan_type.like(like))
                        | (Loan.notes.like(like)))
                .limit(60).all())
        add("loans", "Loans", rows, lambda r: {
            "id": r.id, "title": r.lender or "Loan",
            "sub": r.loan_type or "", "amount": float(r.outstanding or r.principal or 0),
            "route": "loans"})

    if wants("cards"):
        rows = (db.query(CreditCard).filter(CreditCard.user_id == uid)
                .filter((CreditCard.bank.like(like)) | (CreditCard.last4.like(like)))
                .limit(60).all())
        add("cards", "Cards", rows, lambda r: {
            "id": r.id, "title": r.bank or "Card",
            "sub": f"•••• {r.last4}" if r.last4 else "", "route": "cards"})

    if wants("insurance"):
        rows = (db.query(Insurance).filter(Insurance.user_id == uid)
                .filter((Insurance.policy_type.like(like)) | (Insurance.provider.like(like))
                        | (Insurance.policy_no.like(like)))
                .limit(60).all())
        add("insurance", "Insurance", rows, lambda r: {
            "id": r.id, "title": r.provider or r.policy_type or "Policy",
            "sub": r.policy_type or "", "route": "insurance"})

    if wants("investments"):
        rows = (db.query(Investment).filter(Investment.user_id == uid)
                .filter((Investment.name.like(like)) | (Investment.broker.like(like))
                        | (Investment.invest_type.like(like)))
                .limit(60).all())
        add("investments", "Investments", rows, lambda r: {
            "id": r.id, "title": r.name or "Investment",
            "sub": r.broker or r.invest_type or "", "route": "investments"})

    if wants("todos"):
        rows = (db.query(Todo).filter(Todo.user_id == uid, Todo.title.like(like))
                .limit(60).all())
        add("todos", "Tasks", rows, lambda r: {
            "id": r.id, "title": r.title, "sub": "", "route": "todo"})

    if wants("vault"):
        # Titles only. The secret itself is encrypted and is never searched — that
        # is the entire point of the vault.
        rows = (db.query(VaultItem).filter(VaultItem.user_id == uid)
                .filter((VaultItem.title.like(like)) | (VaultItem.username.like(like)))
                .limit(60).all())
        add("vault", "Passwords", rows, lambda r: {
            "id": r.id, "title": r.title, "sub": r.username or "", "route": "vault"})

    if wants("photos"):
        if browsing:
            recent = (db.query(GalleryPhoto)
                      .filter(GalleryPhoto.user_id == uid, GalleryPhoto.is_trashed == 0)
                      .order_by(GalleryPhoto.taken_at.desc()).limit(limit).all())
            add("photos", "Photos", [_photo_row(p, "browse") for p in recent], lambda r: r)
        else:
            add("photos", "Photos", _photos(db, uid, text, intent, limit), lambda r: r)

    total = sum(g["count"] for g in groups)
    understood = {k: v for k, v in {
        "modules": sorted(wanted), "year": intent["year"], "month": intent["month"],
    }.items() if v}
    return {"query": text, "groups": groups, "total": total, "understood": understood}


# Below this many photos, CLIP's nearest neighbour means nothing: it returns the
# closest of a handful whatever is asked. Measured on this library — "qwertyuiop"
# scored HIGHER than "food" (0.247 vs 0.222), and a z-score against the library
# mean separated them no better. Raw CLIP cosine is not comparable across
# queries, so no threshold can fix it; only having enough photos can.
CLIP_MIN_LIBRARY = 50


def _photos(db, uid: int, text: str, intent: dict, limit: int) -> list[dict]:
    """Photos by their words first, then by what they look like.

    Words win outright: a caption, a filename, or text read out of the picture is
    a fact. Visual similarity is a guess, and is only offered when the library is
    big enough for the guess to carry information.
    """
    like = f"%{text}%"
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == uid, GalleryPhoto.is_trashed == 0)
            .filter((GalleryPhoto.caption.like(like)) | (GalleryPhoto.orig_name.like(like))
                    | (GalleryPhoto.ocr_text.like(like)))
            .order_by(GalleryPhoto.taken_at.desc()).limit(limit).all())
    seen = {r.id for r in rows}
    out = [_photo_row(p, matched="text") for p in rows]

    if len(out) < limit and vision.clip_available():
        library = (db.query(GalleryPhoto)
                   .filter(GalleryPhoto.user_id == uid, GalleryPhoto.is_trashed == 0)
                   .count())
        if library >= CLIP_MIN_LIBRARY:
            try:
                from .. import indexer
                for pid, _score in indexer.search(db, uid, text, limit=limit * 2):
                    if pid in seen:
                        continue
                    photo = db.query(GalleryPhoto).filter(GalleryPhoto.id == pid).first()
                    if photo and not photo.is_trashed:
                        # Flagged as a resemblance, not a match, so the interface
                        # can say so rather than implying the words were found.
                        out.append(_photo_row(photo, matched="looks"))
                        seen.add(pid)
                    if len(out) >= limit:
                        break
            except Exception:
                pass
    return out


def _photo_row(p, matched: str) -> dict:
    return {"id": p.id, "title": p.caption or p.orig_name or "Photo",
            "sub": ist.fmt(p.taken_at, with_time=False) or "",
            "thumb_url": media_url(p.user_id, storage.THUMB, p.filename),
            "matched": matched, "route": "gallery"}
