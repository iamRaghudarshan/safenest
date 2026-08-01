"""User-managed master lists ('masters') — custom categories, banks, and other
lookup values used across the app. Per-user; defaults are lazily seeded on first
read so every value (including built-ins) can be renamed, hidden, or extended."""
import re
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit
from ..models import Master, User
from ..security import get_current_user

router = APIRouter(prefix="/api/masters", tags=["masters"])

# type -> {label, field: which attribute the UI edits, defaults: [(key,label,emoji_or_color)]}
MASTER_TYPES: dict[str, dict] = {
    "document_category": {"label": "Document categories", "field": "emoji", "defaults": [
        ("id", "ID Cards", "🪪"), ("financial", "Financial", "💳"), ("insurance", "Insurance", "🛡️"),
        ("vehicle", "Vehicle", "🚗"), ("property", "Property", "🏠"), ("medical", "Medical", "🏥"),
        ("education", "Education", "🎓"), ("other", "Other", "📄"),
    ]},
    "bank": {"label": "Banks", "field": "color", "defaults": [
        ("hdfc", "HDFC Bank", "#004c8f"), ("icici", "ICICI Bank", "#af272f"), ("sbi", "State Bank of India", "#22409a"),
        ("axis", "Axis Bank", "#97144d"), ("kotak", "Kotak Mahindra", "#ed1c24"), ("pnb", "Punjab National Bank", "#a10f2b"),
        ("yes", "Yes Bank", "#0c4da2"), ("idfc", "IDFC First", "#9c1d26"), ("other", "Other", "#64748b"),
    ]},
    "expense_category": {"label": "Expense categories", "field": "emoji", "defaults": [
        ("food", "Food & Dining", "🍔"), ("groceries", "Groceries", "🛒"), ("transport", "Transport", "🚕"),
        ("bills", "Bills & Utilities", "🧾"), ("shopping", "Shopping", "🛍️"), ("health", "Health", "💊"),
        ("entertainment", "Entertainment", "🎬"), ("travel", "Travel", "✈️"), ("other", "Other", "💸"),
    ]},
    "vault_category": {"label": "Vault categories", "field": "emoji", "defaults": [
        ("bank", "Banking", "🏦"), ("email", "Email", "✉️"), ("social", "Social", "💬"),
        ("work", "Work", "💼"), ("shopping", "Shopping", "🛍️"), ("other", "Other", "🔑"),
    ]},
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-") or "item"


def _present(m: Master) -> dict:
    return {"id": m.id, "type": m.type, "key": m.key, "label": m.label,
            "emoji": m.emoji, "color": m.color, "sort_order": m.sort_order or 0,
            "is_active": int(m.is_active if m.is_active is not None else 1)}


def _seed(db: Session, uid: int, mtype: str) -> None:
    meta = MASTER_TYPES[mtype]
    now = ist.now()
    for i, (key, label, extra) in enumerate(meta["defaults"]):
        emoji = extra if meta["field"] == "emoji" else None
        color = extra if meta["field"] == "color" else None
        db.add(Master(user_id=uid, type=mtype, key=key, label=label, emoji=emoji, color=color,
                      sort_order=i, is_active=1, created_at=now, updated_at=now))
    db.commit()


@router.get("/types")
def types(user: User = Depends(get_current_user)):
    return {"types": [{"type": t, "label": m["label"], "field": m["field"]}
                      for t, m in MASTER_TYPES.items()]}


@router.get("")
def index(type: str, active: int = 0, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if type not in MASTER_TYPES:
        raise HTTPException(404, "Unknown master type")
    q = db.query(Master).filter(Master.user_id == user.id, Master.type == type)
    if q.count() == 0:
        _seed(db, user.id, type)
    rows = (db.query(Master).filter(Master.user_id == user.id, Master.type == type)
            .order_by(Master.sort_order.asc(), Master.id.asc()).all())
    if active:
        rows = [r for r in rows if r.is_active]
    return {"type": type, "field": MASTER_TYPES[type]["field"], "items": [_present(r) for r in rows]}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mtype = body.get("type")
    if mtype not in MASTER_TYPES:
        raise HTTPException(404, "Unknown master type")
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(422, "Label is required")
    field = MASTER_TYPES[mtype]["field"]
    key = _slug(body.get("key") or label)
    # ensure key unique within (user, type)
    base, n = key, 1
    while db.query(Master).filter(Master.user_id == user.id, Master.type == mtype, Master.key == key).first():
        n += 1; key = f"{base}-{n}"
    nxt = (db.query(Master).filter(Master.user_id == user.id, Master.type == mtype).count())
    now = ist.now()
    m = Master(user_id=user.id, type=mtype, key=key, label=label,
               emoji=(body.get("emoji") or None) if field == "emoji" else None,
               color=(body.get("color") or None) if field == "color" else None,
               sort_order=nxt, is_active=1, created_at=now, updated_at=now)
    db.add(m); db.commit(); db.refresh(m)
    audit(db, user.id, "create", "master", m.id, {"type": mtype, "label": label})
    return {"item": _present(m)}


def _owned(db: Session, uid: int, mid: int) -> Master:
    m = db.query(Master).filter(Master.id == mid, Master.user_id == uid).first()
    if not m:
        raise HTTPException(404, "Not found")
    return m


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _owned(db, user.id, id)
    field = MASTER_TYPES.get(m.type, {}).get("field", "emoji")
    if "label" in body and (body["label"] or "").strip():
        m.label = body["label"].strip()
    if field == "emoji" and "emoji" in body:
        m.emoji = body["emoji"] or None
    if field == "color" and "color" in body:
        m.color = body["color"] or None
    if "is_active" in body:
        m.is_active = 1 if body["is_active"] else 0
    if "sort_order" in body:
        try:
            m.sort_order = int(body["sort_order"])
        except (TypeError, ValueError):
            pass
    m.updated_at = ist.now()
    db.commit()
    return {"item": _present(m)}


@router.delete("/{id}")
def destroy(id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = _owned(db, user.id, id)
    label = m.label
    db.delete(m); db.commit()
    audit(db, user.id, "delete", "master", id, {"label": label})
    return {"deleted": id}
