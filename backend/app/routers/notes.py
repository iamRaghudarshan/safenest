"""Notes — a Google-Keep-style module: free-text notes and checklists, with a
colour, labels, pin and archive, plus a recycle bin and search.

A note is either kind='note' (its text is in `body`) or kind='checklist' (its
lines are NoteItem rows). Labels are stored as a JSON array on the note; the list
endpoint also returns every label in use so a client can offer them. Images and a
per-note reminder are stage 2 — the `reminder_at` column already exists so it need
not be migrated in later.
"""
import json

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, to_dict
from ..models import Note, NoteItem, User
from ..security import guard

router = APIRouter(prefix="/api/notes", tags=["notes"])

# The Keep-like palette. Anything else falls back to 'default' rather than being
# stored, so a client typo cannot put an unrenderable colour on a note.
COLORS = {"default", "red", "orange", "yellow", "green", "teal", "blue",
          "darkblue", "purple", "pink", "brown", "grey"}


def _labels(raw) -> list:
    """A clean list of label strings from either a list or the stored JSON."""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    try:
        v = json.loads(raw or "[]")
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
    except Exception:
        return []


def _present(n: Note, db: Session) -> dict:
    d = to_dict(n)
    d["labels"] = _labels(n.labels)
    d["pinned"] = bool(n.pinned)
    d["archived"] = bool(n.archived)
    items = (db.query(NoteItem).filter(NoteItem.note_id == n.id)
             .order_by(NoteItem.position.asc(), NoteItem.id.asc()).all())
    d["items"] = [{"id": it.id, "text": it.text, "checked": bool(it.checked),
                   "position": it.position} for it in items]
    return d


def _set_items(db: Session, note: Note, user: User, items) -> None:
    """Replace a checklist note's lines with the given list. Empty lines drop out,
    the way Keep discards a blank checklist row."""
    db.query(NoteItem).filter(NoteItem.note_id == note.id).delete()
    pos = 0
    for it in (items or []):
        if isinstance(it, dict):
            text = (it.get("text") or "").strip()
            checked = 1 if it.get("checked") else 0
        else:
            text, checked = str(it).strip(), 0
        if not text:
            continue
        db.add(NoteItem(note_id=note.id, user_id=user.id, text=text[:1000],
                        checked=checked, position=pos))
        pos += 1


@router.get("")
def index(bucket: str = "active", label: str = "", q: str = "",
          user: User = Depends(guard("notes", "view")), db: Session = Depends(get_db)):
    sel = db.query(Note).filter(Note.user_id == user.id)
    b = (bucket or "active").lower()
    if b == "archived":
        sel = sel.filter(Note.archived == 1, Note.is_trashed == 0)
    elif b == "trashed":
        sel = sel.filter(Note.is_trashed == 1)
    else:                                      # active: not archived, not binned
        sel = sel.filter(Note.archived == 0, Note.is_trashed == 0)

    if q.strip():
        like = f"%{q.strip()}%"
        hit_items = [r[0] for r in db.query(NoteItem.note_id).filter(
            NoteItem.user_id == user.id, NoteItem.text.ilike(like))]
        sel = sel.filter(or_(Note.title.ilike(like), Note.body.ilike(like),
                             Note.id.in_(hit_items) if hit_items else Note.id == -1))

    # Pinned first, then manual order, then most-recently-touched — Keep's order.
    rows = sel.order_by(Note.pinned.desc(), Note.position.asc(),
                        Note.updated_at.desc()).limit(1000).all()
    out = [_present(r, db) for r in rows]
    if label.strip():
        out = [n for n in out if label.strip() in n["labels"]]

    # Every label in use (excluding the bin), for the client's label rail.
    all_labels = sorted({lab for r in db.query(Note.labels).filter(
        Note.user_id == user.id, Note.is_trashed == 0) for lab in _labels(r[0])})
    return {"items": out, "labels": all_labels}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("notes", "create")),
           db: Session = Depends(get_db)):
    now = ist.now()
    kind = "checklist" if body.get("kind") == "checklist" else "note"
    color = body.get("color") if body.get("color") in COLORS else "default"
    n = Note(user_id=user.id, title=(body.get("title") or "").strip()[:255],
             body=(body.get("body") or "") if kind == "note" else "",
             kind=kind, color=color,
             labels=json.dumps(_labels(body.get("labels"))),
             pinned=1 if body.get("pinned") else 0,
             archived=0, is_trashed=0, position=0,
             created_at=now, updated_at=now)
    db.add(n); db.commit(); db.refresh(n)
    if kind == "checklist":
        _set_items(db, n, user, body.get("items"))
        db.commit()
    audit(db, user.id, "create", "note", n.id, {"label": n.title or "(note)"})
    return {"item": _present(n, db)}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("notes", "edit")),
           db: Session = Depends(get_db)):
    n = db.query(Note).filter(Note.id == id, Note.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "note not found")
    if "title" in body:
        n.title = (body.get("title") or "").strip()[:255]
    if "kind" in body and body["kind"] in ("note", "checklist"):
        n.kind = body["kind"]
    if "body" in body:
        n.body = body.get("body") or ""
    if "color" in body and body["color"] in COLORS:
        n.color = body["color"]
    if "labels" in body:
        n.labels = json.dumps(_labels(body.get("labels")))
    if "items" in body:
        _set_items(db, n, user, body.get("items"))
    n.updated_at = ist.now(); db.commit(); db.refresh(n)
    audit(db, user.id, "update", "note", id, {"label": n.title or "(note)"})
    return {"item": _present(n, db)}


@router.post("/{id}/pin")
def pin(id: int, user: User = Depends(guard("notes", "edit")), db: Session = Depends(get_db)):
    n = db.query(Note).filter(Note.id == id, Note.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "note not found")
    n.pinned = 0 if n.pinned else 1
    n.updated_at = ist.now(); db.commit()
    return {"id": id, "pinned": bool(n.pinned)}


@router.post("/{id}/archive")
def archive(id: int, user: User = Depends(guard("notes", "edit")), db: Session = Depends(get_db)):
    n = db.query(Note).filter(Note.id == id, Note.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "note not found")
    n.archived = 0 if n.archived else 1
    # Un-pin on archive, the way Keep does — a pinned note in the archive would
    # sit above the ones you actually kept out.
    if n.archived:
        n.pinned = 0
    n.updated_at = ist.now(); db.commit()
    return {"id": id, "archived": bool(n.archived)}


@router.post("/{id}/trash")
def trash(id: int, user: User = Depends(guard("notes", "delete")), db: Session = Depends(get_db)):
    n = db.query(Note).filter(Note.id == id, Note.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "note not found")
    n.is_trashed = 1
    n.updated_at = ist.now(); db.commit()
    audit(db, user.id, "trash", "note", id, {"label": n.title or "(note)"})
    return {"id": id, "trashed": True}


@router.post("/{id}/restore")
def restore(id: int, user: User = Depends(guard("notes", "edit")), db: Session = Depends(get_db)):
    n = db.query(Note).filter(Note.id == id, Note.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "note not found")
    n.is_trashed = 0
    n.updated_at = ist.now(); db.commit()
    return {"id": id, "trashed": False}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("notes", "delete")), db: Session = Depends(get_db)):
    """Delete for good — its checklist lines go with it."""
    n = db.query(Note).filter(Note.id == id, Note.user_id == user.id).first()
    if not n:
        raise HTTPException(404, "note not found")
    db.query(NoteItem).filter(NoteItem.note_id == id).delete()
    label = n.title
    db.delete(n); db.commit()
    audit(db, user.id, "delete", "note", id, {"label": label or "(note)"})
    return {"deleted": id}
