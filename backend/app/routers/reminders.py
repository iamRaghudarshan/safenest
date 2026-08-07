from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..digest import pretty_time
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Reminder, User
from ..security import guard

router = APIRouter(prefix="/api/reminders", tags=["reminders"])
FIELDS = ["title", "module_ref", "due_date", "due_time", "recurrence",
          "notify_push", "notify_email", "is_done"]


def _clean_time(v) -> str | None:
    """'HH:MM' or nothing at all — never a value the scheduler will skip silently.

    A reminder's whole purpose is to arrive. A time it cannot read is worse than
    no time, because the reminder still looks set: it sits in the list showing
    whatever was typed, and the hour comes and goes. So an unreadable time is
    refused at the door rather than stored and ignored.

    A browser's <input type="time"> sends "18:30", or "18:30:00" when seconds are
    enabled, so the seconds are accepted and dropped.
    """
    if v in ("", None):
        return None
    s = str(v).strip()
    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise HTTPException(422, "Time should look like 18:30")
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        raise HTTPException(422, "Time should look like 18:30")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise HTTPException(422, "Time should look like 18:30")
    return f"{hh:02d}:{mm:02d}"


def _present(r: Reminder) -> dict:
    d = to_dict(r)
    if r.due_date:
        d["days"] = (r.due_date - ist.today()).days
        d["due_fmt"] = r.due_date.strftime("%d-%m-%Y")
        if r.due_time:
            d["due_fmt"] = f"{d['due_fmt']} at {pretty_time(r.due_time)}"
            d["time_fmt"] = pretty_time(r.due_time)
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
    r.due_time = _clean_time(body.get("due_time"))
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
    # The only field here that can be taken back off. Sending "" for a name would
    # be a mistake and is ignored above; sending "" for a time means "stop ringing
    # at an hour, just remind me on the day", and there has to be a way to say it.
    if "due_time" in body:
        r.due_time = _clean_time(body["due_time"])
    # Moving a reminder re-arms it. Without this, one that already fired today
    # would stay silent when pushed to a later hour of the same day — the alarm
    # you just set is the one least likely to be forgiven for not going off.
    if ("due_time" in body or "due_date" in body) and (
            r.due_time != before.get("due_time") or str(r.due_date) != str(before.get("due_date"))):
        r.notified_on = None
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
    # Ticking something off and putting it back is how people correct a misfire,
    # and the reminder they reopen is one they want to hear about again.
    if not r.is_done:
        r.notified_on = None
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
