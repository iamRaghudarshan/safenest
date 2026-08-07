"""User-managed master lists ('masters') — custom categories, banks, and other
lookup values used across the app. Per-user; defaults are lazily seeded on first
read so every value (including built-ins) can be renamed, hidden, or extended."""
import re
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit
from ..models import Master, MasterList, User
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


# ---------------------------------------------------------------- the lists ---
#
# MASTER_TYPES above is now the SEED, not the whole set. A person can add lists
# of their own — insurers, landlords, payment methods — and the four built-ins
# are rows like any other so that they can be renamed too.


def _seed_lists(db: Session, uid: int) -> None:
    """Put the four built-ins in the table for a user who has none yet.

    Lazy, exactly like the values themselves, so an installation that never
    opens this screen is untouched until the moment something asks.
    """
    now = ist.now()
    for i, (t, meta) in enumerate(MASTER_TYPES.items()):
        db.add(MasterList(user_id=uid, type=t, label=meta["label"],
                          field=meta["field"], icon=meta.get("icon"),
                          is_builtin=1, sort_order=i,
                          created_at=now, updated_at=now))
    db.commit()


def _lists(db: Session, uid: int) -> list[MasterList]:
    q = db.query(MasterList).filter(MasterList.user_id == uid)
    if q.count() == 0:
        _seed_lists(db, uid)
    return (db.query(MasterList).filter(MasterList.user_id == uid)
            .order_by(MasterList.sort_order.asc(), MasterList.id.asc()).all())


def _list_of(db: Session, uid: int, mtype: str) -> MasterList:
    """The list a type names, or 404.

    Everything that used to test `type not in MASTER_TYPES` goes through here
    instead, which is what makes a user-defined list a first-class one rather
    than a special case bolted on beside the built-ins.
    """
    for row in _lists(db, uid):
        if row.type == mtype:
            return row
    raise HTTPException(404, "Unknown list")


def _present_list(row: MasterList, count: int = 0) -> dict:
    return {"id": row.id, "type": row.type, "label": row.label,
            "field": row.field or "emoji", "icon": row.icon,
            "is_builtin": int(row.is_builtin or 0),
            "sort_order": row.sort_order or 0, "count": count}


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
def types(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = _lists(db, user.id)
    counts = dict(
        db.query(Master.type, func.count(Master.id))
        .filter(Master.user_id == user.id).group_by(Master.type).all())
    return {"types": [_present_list(r, int(counts.get(r.type, 0))) for r in rows]}


@router.post("/types")
def create_type(body: dict = Body(...), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Add a list of your own — insurers, landlords, whatever this person keeps."""
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(422, "Give the list a name")
    field = body.get("field") if body.get("field") in ("emoji", "color") else "emoji"

    existing = _lists(db, user.id)
    taken = {r.type for r in existing}
    # A custom list must never collide with a built-in slug: the app's own forms
    # ask for `expense_category` by name, and a second list answering to it would
    # quietly replace the one the product reads.
    key = base = _slug(label)
    n = 1
    while key in taken:
        n += 1
        key = f"{base}-{n}"

    now = ist.now()
    row = MasterList(user_id=user.id, type=key, label=label, field=field,
                     icon=(body.get("icon") or None), is_builtin=0,
                     sort_order=len(existing), created_at=now, updated_at=now)
    db.add(row); db.commit(); db.refresh(row)
    audit(db, user.id, "create", "master_list", row.id, {"label": label})
    return {"item": _present_list(row)}


@router.put("/types/{id}")
def update_type(id: int, body: dict = Body(...), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Rename a list, or change its icon. Built-ins included — "all of them can
    be edited" — but never its `type`, which is the identity every value in it
    points at and which the product's own forms name in code."""
    row = (db.query(MasterList)
           .filter(MasterList.id == id, MasterList.user_id == user.id).first())
    if not row:
        raise HTTPException(404, "Not found")
    if "label" in body and (body["label"] or "").strip():
        row.label = body["label"].strip()
    if "icon" in body:
        row.icon = body["icon"] or None
    # The extra a list carries is only changeable on a custom one. Flipping a
    # built-in from emoji to colour would strip the glyphs off values the
    # product ships with and that other screens draw.
    if not row.is_builtin and body.get("field") in ("emoji", "color"):
        row.field = body["field"]
    if "sort_order" in body:
        try:
            row.sort_order = int(body["sort_order"])
        except (TypeError, ValueError):
            pass
    row.updated_at = ist.now()
    db.commit(); db.refresh(row)
    audit(db, user.id, "update", "master_list", id, {"label": row.label})
    return {"item": _present_list(row)}


@router.delete("/types/{id}")
def delete_type(id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Remove a list you added, and everything in it.

    A built-in is refused rather than hidden. `expense_category` is asked for by
    name by the expense form on two clients; deleting it would leave a field
    with nothing to offer and no way in the interface to get it back.
    """
    row = (db.query(MasterList)
           .filter(MasterList.id == id, MasterList.user_id == user.id).first())
    if not row:
        raise HTTPException(404, "Not found")
    if row.is_builtin:
        raise HTTPException(
            422, f"“{row.label}” is one of the built-in lists and cannot be "
                 "removed. You can rename it, or hide the entries you never use.")
    label, mtype = row.label, row.type
    # The values go with it. Leaving them would be rows pointing at a list that
    # no longer exists — invisible, un-editable, and still counted.
    gone = (db.query(Master)
            .filter(Master.user_id == user.id, Master.type == mtype)
            .delete(synchronize_session=False))
    db.delete(row); db.commit()
    audit(db, user.id, "delete", "master_list", id,
          {"label": label, "values_removed": int(gone or 0)})
    return {"deleted": id, "values_removed": int(gone or 0)}


@router.get("")
def index(type: str, active: int = 0, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    lst = _list_of(db, user.id, type)
    q = db.query(Master).filter(Master.user_id == user.id, Master.type == type)
    # Only a BUILT-IN has defaults to seed. A list somebody just made starts
    # empty, and seeding it would be inventing entries they did not ask for.
    if q.count() == 0 and type in MASTER_TYPES:
        _seed(db, user.id, type)
    rows = (db.query(Master).filter(Master.user_id == user.id, Master.type == type)
            .order_by(Master.sort_order.asc(), Master.id.asc()).all())
    if active:
        rows = [r for r in rows if r.is_active]
    return {"type": type, "label": lst.label, "field": lst.field or "emoji",
            "is_builtin": int(lst.is_builtin or 0),
            "items": [_present(r) for r in rows]}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    mtype = body.get("type")
    lst = _list_of(db, user.id, mtype)
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(422, "Label is required")
    field = lst.field or "emoji"
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
    field = (_list_of(db, user.id, m.type).field or "emoji")
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
