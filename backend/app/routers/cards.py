import calendar
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import CardPayment, CreditCard, Reminder, User
from ..security import guard

router = APIRouter(prefix="/api/cards", tags=["cards"])
FIELDS = ["bank", "last4", "credit_limit", "billing_day", "due_date"]


def _due_on(day: int, y: int, m: int) -> date:
    day = max(1, min(day, 31))
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day, last))


def _payment_status(card: CreditCard, db: Session, today: date) -> dict:
    """Paid/unpaid for the CURRENT calendar month; the due shown is this month's
    bill if unpaid, else next month's. Also returns the most-recent paid date."""
    day = int(card.billing_day or (card.due_date.day if card.due_date else 1))
    period = today.strftime("%Y-%m")
    pay = (db.query(CardPayment)
           .filter(CardPayment.card_id == card.id, CardPayment.period == period).first())
    last = (db.query(CardPayment)
            .filter(CardPayment.card_id == card.id)
            .order_by(CardPayment.paid_date.desc()).first())

    if pay:  # this month is paid → next actionable bill is next month
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        due = _due_on(day, ny, nm)
    else:
        due = _due_on(day, today.year, today.month)

    return {
        "due_day": day,
        "period": period,
        "paid_this_month": bool(pay),
        "paid_date": pay.paid_date.strftime("%d-%m-%Y") if pay and pay.paid_date else None,
        "last_paid": last.paid_date.strftime("%d-%m-%Y") if last and last.paid_date else None,
        "next_due": due.isoformat(),
        "next_due_fmt": due.strftime("%d-%m-%Y"),
        "days_until": (due - today).days,
    }


@router.get("")
def index(user: User = Depends(guard("cards", "view")), db: Session = Depends(get_db)):
    today = ist.today()
    out = []
    for c in db.query(CreditCard).filter(CreditCard.user_id == user.id).limit(500):
        d = to_dict(c)
        d.update(_payment_status(c, db, today))
        out.append(d)
    out.sort(key=lambda x: x["days_until"])
    return {"items": out}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("cards", "create")), db: Session = Depends(get_db)):
    now = ist.now()
    c = CreditCard(user_id=user.id, created_at=now, updated_at=now)
    for f in FIELDS:
        if body.get(f) not in ("", None):
            setattr(c, f, body[f])
    db.add(c); db.commit(); db.refresh(c)
    if c.due_date:  # auto monthly reminder
        title = f"{c.bank or 'Card'} bill" + (f" ••{c.last4}" if c.last4 else "")
        db.add(Reminder(user_id=user.id, title=title, module_ref="cards", due_date=c.due_date,
                        recurrence="monthly", notify_push=1, created_at=now, updated_at=now))
        db.commit()
    audit(db, user.id, "create", "card", c.id, {"label": f"{c.bank} ••{c.last4}"})
    return {"item": to_dict(c)}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("cards", "edit")), db: Session = Depends(get_db)):
    c = db.query(CreditCard).filter(CreditCard.id == id, CreditCard.user_id == user.id).first()
    if not c:
        raise HTTPException(404, "card not found")
    before = snapshot(c)
    for f in FIELDS:
        if f in body and body[f] not in ("", None):
            setattr(c, f, body[f])
    c.updated_at = ist.now(); db.commit(); db.refresh(c)
    audit(db, user.id, "update", "card", id,
          {"label": (c.card_name or c.bank), "changes": changes(before, snapshot(c))})
    return {"item": to_dict(c)}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("cards", "delete")), db: Session = Depends(get_db)):
    c = db.query(CreditCard).filter(CreditCard.id == id, CreditCard.user_id == user.id).first()
    if not c:
        raise HTTPException(404, "card not found")
    label = f"{c.bank} ••{c.last4}"
    db.query(CardPayment).filter(CardPayment.card_id == id).delete()
    db.delete(c); db.commit()
    audit(db, user.id, "delete", "card", id, {"label": label})
    return {"deleted": id}


def _owned_card(id: int, user: User, db: Session) -> CreditCard:
    c = db.query(CreditCard).filter(CreditCard.id == id, CreditCard.user_id == user.id).first()
    if not c:
        raise HTTPException(404, "card not found")
    return c


@router.post("/{id}/pay")
def mark_paid(id: int, body: dict = Body(default={}), user: User = Depends(guard("cards", "edit")), db: Session = Depends(get_db)):
    """Mark a month's bill paid. Defaults to the current month, paid today."""
    c = _owned_card(id, user, db)
    today = ist.today()
    period = body.get("period") or today.strftime("%Y-%m")
    paid_date = body.get("paid_date") or today.isoformat()
    row = db.query(CardPayment).filter(CardPayment.card_id == id, CardPayment.period == period).first()
    if row:
        row.paid_date = paid_date
        if body.get("amount") not in (None, ""):
            row.amount = body["amount"]
    else:
        row = CardPayment(card_id=id, user_id=user.id, period=period, paid_date=paid_date,
                          amount=body.get("amount") or c.statement_amount or None, created_at=ist.now())
        db.add(row)
    db.commit()
    audit(db, user.id, "card_paid", "card", id, {"label": f"{c.bank} ••{c.last4}", "period": period})
    return {"id": id, "period": period, "paid_date": paid_date}


@router.post("/{id}/unpay")
def mark_unpaid(id: int, body: dict = Body(default={}), user: User = Depends(guard("cards", "edit")), db: Session = Depends(get_db)):
    """Undo a payment for a month (defaults to current month)."""
    _owned_card(id, user, db)
    period = body.get("period") or ist.today().strftime("%Y-%m")
    db.query(CardPayment).filter(CardPayment.card_id == id, CardPayment.period == period).delete()
    db.commit()
    return {"id": id, "period": period, "paid": False}


@router.get("/{id}/payments")
def payments(id: int, user: User = Depends(guard("cards", "view")), db: Session = Depends(get_db)):
    _owned_card(id, user, db)
    rows = (db.query(CardPayment).filter(CardPayment.card_id == id)
            .order_by(CardPayment.period.desc()).limit(36).all())

    def _label(period: str) -> str:
        try:
            y, m = period.split("-")
            return f"{calendar.month_abbr[int(m)]} {y}"
        except Exception:
            return period

    return {"items": [{
        "period": r.period, "period_label": _label(r.period),
        "paid_date": r.paid_date.strftime("%d-%m-%Y") if r.paid_date else None,
        "amount": float(r.amount) if r.amount is not None else None,
    } for r in rows]}
