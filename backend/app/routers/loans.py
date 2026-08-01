import calendar
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Loan, LoanPayment, Reminder, User
from ..security import guard

router = APIRouter(prefix="/api/loans", tags=["loans"])
FIELDS = ["lender", "loan_type", "principal", "interest_rate", "emi", "tenure_months",
          "outstanding", "start_date", "next_due_date", "status", "notes"]


def _due_on(day: int, y: int, m: int) -> date:
    day = max(1, min(day, 31))
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day, last))


def _emi_status(loan: Loan, db: Session, today: date) -> dict:
    """Paid/unpaid EMI for the CURRENT calendar month; the due shown is this
    month's EMI if unpaid, else next month's. Also the most-recent paid date."""
    src = loan.next_due_date or loan.start_date
    day = src.day if src else 1
    period = today.strftime("%Y-%m")
    pay = (db.query(LoanPayment)
           .filter(LoanPayment.loan_id == loan.id, LoanPayment.period == period).first())
    last = (db.query(LoanPayment)
            .filter(LoanPayment.loan_id == loan.id)
            .order_by(LoanPayment.paid_date.desc()).first())

    if pay:
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        due = _due_on(day, ny, nm)
    else:
        due = _due_on(day, today.year, today.month)

    return {
        "emi_day": day,
        "period": period,
        "paid_this_month": bool(pay),
        "paid_date": pay.paid_date.strftime("%d-%m-%Y") if pay and pay.paid_date else None,
        "last_paid": last.paid_date.strftime("%d-%m-%Y") if last and last.paid_date else None,
        "next_emi": due.isoformat(),
        "next_emi_fmt": due.strftime("%d-%m-%Y"),
        "days_until": (due - today).days,
    }


@router.get("")
def index(user: User = Depends(guard("loans", "view")), db: Session = Depends(get_db)):
    today = ist.today()
    rows = db.query(Loan).filter(Loan.user_id == user.id).limit(500).all()
    out = []
    for r in rows:
        d = to_dict(r)
        d.update(_emi_status(r, db, today))
        out.append(d)
    out.sort(key=lambda x: x["days_until"])
    return {"items": out}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("loans", "create")), db: Session = Depends(get_db)):
    now = ist.now()
    obj = Loan(user_id=user.id, created_at=now, updated_at=now)
    for f in FIELDS:
        if body.get(f) not in ("", None):
            setattr(obj, f, body[f])
    db.add(obj); db.commit(); db.refresh(obj)
    if obj.next_due_date:  # auto monthly EMI reminder
        title = f"{obj.lender or 'Loan'} EMI" + (f" ₹{int(obj.emi)}" if obj.emi else "")
        db.add(Reminder(user_id=user.id, title=title, module_ref="loans", due_date=obj.next_due_date,
                        recurrence="monthly", notify_push=1, created_at=now, updated_at=now))
        db.commit()
    audit(db, user.id, "create", "loan", obj.id, {"label": obj.lender})
    return {"item": to_dict(obj)}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("loans", "edit")), db: Session = Depends(get_db)):
    obj = db.query(Loan).filter(Loan.id == id, Loan.user_id == user.id).first()
    if not obj:
        raise HTTPException(404, "loan not found")
    before = snapshot(obj)
    for f in FIELDS:
        if f in body and body[f] not in ("", None):
            setattr(obj, f, body[f])
    obj.updated_at = ist.now(); db.commit(); db.refresh(obj)
    audit(db, user.id, "update", "loan", id,
          {"label": obj.lender, "changes": changes(before, snapshot(obj))})
    return {"item": to_dict(obj)}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("loans", "delete")), db: Session = Depends(get_db)):
    obj = db.query(Loan).filter(Loan.id == id, Loan.user_id == user.id).first()
    if not obj:
        raise HTTPException(404, "loan not found")
    label = obj.lender
    db.query(LoanPayment).filter(LoanPayment.loan_id == id).delete()
    db.delete(obj); db.commit()
    audit(db, user.id, "delete", "loan", id, {"label": label})
    return {"deleted": id}


def _owned(id: int, user: User, db: Session) -> Loan:
    obj = db.query(Loan).filter(Loan.id == id, Loan.user_id == user.id).first()
    if not obj:
        raise HTTPException(404, "loan not found")
    return obj


@router.post("/{id}/pay")
def mark_paid(id: int, body: dict = Body(default={}), user: User = Depends(guard("loans", "edit")), db: Session = Depends(get_db)):
    """Mark an EMI paid. Defaults to the current month, paid today."""
    loan = _owned(id, user, db)
    today = ist.today()
    period = body.get("period") or today.strftime("%Y-%m")
    paid_date = body.get("paid_date") or today.isoformat()
    row = db.query(LoanPayment).filter(LoanPayment.loan_id == id, LoanPayment.period == period).first()
    if row:
        row.paid_date = paid_date
        if body.get("amount") not in (None, ""):
            row.amount = body["amount"]
    else:
        row = LoanPayment(loan_id=id, user_id=user.id, period=period, paid_date=paid_date,
                          amount=body.get("amount") or loan.emi or None, created_at=ist.now())
        db.add(row)
    db.commit()
    audit(db, user.id, "loan_paid", "loan", id, {"label": obj.lender, "period": period})
    return {"id": id, "period": period, "paid_date": paid_date}


@router.post("/{id}/unpay")
def mark_unpaid(id: int, body: dict = Body(default={}), user: User = Depends(guard("loans", "edit")), db: Session = Depends(get_db)):
    _owned(id, user, db)
    period = body.get("period") or ist.today().strftime("%Y-%m")
    db.query(LoanPayment).filter(LoanPayment.loan_id == id, LoanPayment.period == period).delete()
    db.commit()
    return {"id": id, "period": period, "paid": False}


@router.get("/{id}/payments")
def payments(id: int, user: User = Depends(guard("loans", "view")), db: Session = Depends(get_db)):
    _owned(id, user, db)
    rows = (db.query(LoanPayment).filter(LoanPayment.loan_id == id)
            .order_by(LoanPayment.period.desc()).limit(60).all())

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
