from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist
from .. import dialect
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Expense, User
from ..security import guard

router = APIRouter(prefix="/api/expenses", tags=["expenses"])
FIELDS = ["kind", "category", "amount", "method", "txn_date", "note"]


@router.get("")
def index(month: str | None = None, kind: str | None = None,
          user: User = Depends(guard("expenses", "view")), db: Session = Depends(get_db)):
    q = db.query(Expense).filter(Expense.user_id == user.id)
    if month:
        q = q.filter(dialect.ym(Expense.txn_date) == month)
    if kind:
        q = q.filter(Expense.kind == kind)
    rows = q.order_by(Expense.txn_date.desc(), Expense.id.desc()).limit(300).all()
    income = sum(float(r.amount) for r in rows if r.kind == "income")
    expense = sum(float(r.amount) for r in rows if r.kind == "expense")
    return {"items": [to_dict(r) for r in rows], "totals": {"income": income, "expense": expense}}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("expenses", "create")), db: Session = Depends(get_db)):
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(422, "Amount must be a number")
    if amount <= 0:
        raise HTTPException(422, "Enter an amount greater than zero")
    if body.get("kind") not in (None, "", "expense", "income"):
        raise HTTPException(422, "Type must be either expense or income")
    now = ist.now()
    # category and txn_date are NOT NULL; fall back rather than letting a cleared
    # field surface as a database error.
    e = Expense(user_id=user.id, kind=body.get("kind") or "expense",
                category=(body.get("category") or "Others"),
                amount=amount, method=body.get("method"),
                txn_date=body.get("txn_date") or ist.today(),
                note=body.get("note"), created_at=now, updated_at=now)
    db.add(e); db.commit(); db.refresh(e)
    audit(db, user.id, "create", "expense", e.id, {"label": f"{e.category} {e.amount}"})
    return {"item": to_dict(e)}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("expenses", "edit")), db: Session = Depends(get_db)):
    e = db.query(Expense).filter(Expense.id == id, Expense.user_id == user.id).first()
    if not e:
        raise HTTPException(404, "expense not found")
    before = snapshot(e)
    for f in FIELDS:
        if f in body and body[f] not in ("", None):
            setattr(e, f, body[f])
    e.updated_at = ist.now(); db.commit(); db.refresh(e)
    audit(db, user.id, "update", "expense", id,
          {"label": f"{e.category} {e.amount}", "changes": changes(before, snapshot(e))})
    return {"item": to_dict(e)}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("expenses", "delete")), db: Session = Depends(get_db)):
    e = db.query(Expense).filter(Expense.id == id, Expense.user_id == user.id).first()
    if not e:
        raise HTTPException(404, "expense not found")
    label = f"{e.category} {e.amount}"
    db.delete(e); db.commit()
    audit(db, user.id, "delete", "expense", id, {"label": label})
    return {"deleted": id}
