from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, to_dict
from ..models import Todo, User
from ..security import guard

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("")
def index(user: User = Depends(guard("todo", "view")), db: Session = Depends(get_db)):
    rows = db.query(Todo).filter(Todo.user_id == user.id).order_by(
        Todo.status.asc(), Todo.due_date.asc()).limit(500).all()
    return {"items": [to_dict(r) for r in rows]}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("todo", "create")), db: Session = Depends(get_db)):
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "Title is required")
    now = ist.now()
    t = Todo(user_id=user.id, title=title, priority=body.get("priority", "medium"),
             due_date=body.get("due_date") or None, status="pending", created_at=now, updated_at=now)
    db.add(t); db.commit(); db.refresh(t)
    audit(db, user.id, "create", "task", t.id, {"label": t.title})
    return {"item": to_dict(t)}


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
