from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Reminder, User
from ..security import guard

router = APIRouter(prefix="/api/reminders", tags=["reminders"])
FIELDS = ["title", "module_ref", "due_date", "recurrence", "notify_push", "notify_email", "is_done"]


def _present(r: Reminder) -> dict:
    d = to_dict(r)
    if r.due_date:
        d["days"] = (r.due_date - ist.today()).days
        d["due_fmt"] = r.due_date.strftime("%d-%m-%Y")
    else:
        d["days"] = None
        d["due_fmt"] = None
    return d


@router.get("")
def index(user: User = Depends(guard("reminders", "view")), db: Session = Depends(get_db)):
    rows = db.query(Reminder).filter(Reminder.user_id == user.id).order_by(
        Reminder.is_done.asc(), Reminder.due_date.asc()).limit(500).all()
    return {"items": [_present(r) for r in rows]}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("reminders", "create")), db: Session = Depends(get_db)):
    if not (body.get("title") or "").strip():
        raise HTTPException(422, "Title is required")
    now = ist.now()
    r = Reminder(user_id=user.id, created_at=now, updated_at=now)
    for f in FIELDS:
        if body.get(f) not in ("", None):
            setattr(r, f, body[f])
    db.add(r); db.commit(); db.refresh(r)
    audit(db, user.id, "create", "reminder", r.id, {"label": r.title})
    return {"item": _present(r)}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("reminders", "edit")), db: Session = Depends(get_db)):
    r = db.query(Reminder).filter(Reminder.id == id, Reminder.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "reminder not found")
    before = snapshot(r)
    for f in FIELDS:
        if f in body and body[f] not in ("", None):
            setattr(r, f, body[f])
    r.updated_at = ist.now(); db.commit(); db.refresh(r)
    audit(db, user.id, "update", "reminder", id,
          {"label": r.title, "changes": changes(before, snapshot(r))})
    return {"item": _present(r)}


@router.post("/{id}/toggle")
def toggle(id: int, user: User = Depends(guard("reminders", "edit")), db: Session = Depends(get_db)):
    r = db.query(Reminder).filter(Reminder.id == id, Reminder.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "reminder not found")
    r.is_done = 0 if r.is_done else 1
    db.commit()
    audit(db, user.id, "done" if r.is_done else "reopen", "reminder", id, {"label": r.title})
    return {"id": id, "is_done": r.is_done}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("reminders", "delete")), db: Session = Depends(get_db)):
    r = db.query(Reminder).filter(Reminder.id == id, Reminder.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "reminder not found")
    label = r.title
    db.delete(r); db.commit()
    audit(db, user.id, "delete", "reminder", id, {"label": label})
    return {"deleted": id}
