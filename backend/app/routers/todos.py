from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import case
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Todo, User
from ..security import guard

router = APIRouter(prefix="/api/todos", tags=["todos"])
FIELDS = ["title", "priority", "due_date", "status", "recurrence"]


def _present(t: Todo) -> dict:
    """Same shape reminders return, so a client can treat the two alike.

    `days` in particular: reminders have always carried it and tasks never did,
    so anything wanting to mark an overdue task had to work the date out itself
    against its own idea of today — which on a phone in another timezone is not
    the same day the server means.
    """
    d = to_dict(t)
    if t.due_date:
        d["days"] = (t.due_date - ist.today()).days
        d["due_fmt"] = t.due_date.strftime("%d-%m-%Y")
    else:
        d["days"] = None
        d["due_fmt"] = None
    return d


@router.get("")
def index(user: User = Depends(guard("todo", "view")), db: Session = Depends(get_db)):
    # Ordered by a CASE, not by the column.
    #
    # `status` is an ENUM on MySQL, where ASC means declaration order and puts
    # 'pending' first — and a plain string on SQLite, where ASC is alphabetical
    # and 'done' comes first. So the same code put finished tasks at the top on
    # exactly the copies we do not run: every customer's. With the limit below
    # that is not only untidy, it hides things — a long history of completed
    # tasks fills the 500 and pushes the outstanding ones off the end.
    rows = db.query(Todo).filter(Todo.user_id == user.id).order_by(
        case((Todo.status == "done", 1), else_=0),
        Todo.due_date.asc()).limit(500).all()
    return {"items": [_present(r) for r in rows]}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("todo", "create")), db: Session = Depends(get_db)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "Title is required")
    now = ist.now()
    t = Todo(user_id=user.id, title=title, priority=body.get("priority", "medium"),
             due_date=body.get("due_date") or None, status="pending",
             # The column and the default have always been here; nothing ever set
             # them, so a repeating task could not be created from any screen.
             recurrence=body.get("recurrence") or "none",
             created_at=now, updated_at=now)
    db.add(t); db.commit(); db.refresh(t)
    audit(db, user.id, "create", "task", t.id, {"label": t.title})
    return {"item": _present(t)}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("todo", "edit")), db: Session = Depends(get_db)):
    """Change a task.

    There was no way to do this at all: the module had create, toggle and delete,
    so a task typed with a typo, or one whose date moved, could only be deleted
    and written out again — losing when it was created and its place in the audit
    log. Every other module has had an edit since the beginning; this one was
    simply missed, and neither screen showed a pencil because neither could.
    """
    t = db.query(Todo).filter(Todo.id == id, Todo.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "task not found")
    if "title" in body and not (body.get("title") or "").strip():
        raise HTTPException(422, "Title is required")
    before = snapshot(t)
    for f in FIELDS:
        if f in body and body[f] not in ("", None):
            setattr(t, f, body[f])
    # A date is the one thing here worth being able to take back off — a task
    # that turned out not to have a deadline should not be stuck with a wrong one.
    if body.get("due_date") in ("", None) and "due_date" in body:
        t.due_date = None
    t.updated_at = ist.now(); db.commit(); db.refresh(t)
    audit(db, user.id, "update", "task", id,
          {"label": t.title, "changes": changes(before, snapshot(t))})
    return {"item": _present(t)}


@router.post("/{id}/toggle")
def toggle(id: int, user: User = Depends(guard("todo", "edit")), db: Session = Depends(get_db)):
    t = db.query(Todo).filter(Todo.id == id, Todo.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "task not found")
    t.status = "pending" if t.status == "done" else "done"
    db.commit()
    audit(db, user.id, "done" if t.status == "done" else "reopen", "task", id, {"label": t.title})
    return {"id": id, "status": t.status}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("todo", "delete")), db: Session = Depends(get_db)):
    t = db.query(Todo).filter(Todo.id == id, Todo.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "task not found")
    label = t.title
    db.delete(t); db.commit()
    audit(db, user.id, "delete", "task", id, {"label": label})
    return {"deleted": id}
