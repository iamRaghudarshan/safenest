"""Daily Briefing — the 'reason to open every day': Safe-to-Spend today,
bills due now (one-tap payable), your logging streak, and a photo memory."""
import calendar
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist
from .. import dialect
from ..database import get_db
from ..models import (CardPayment, CreditCard, Expense, GalleryPhoto, Insurance,
                      Loan, LoanPayment, Reminder, Todo, User)
from ..security import get_current_user
from ..storage import THUMB
from .gallery import media_url

router = APIRouter(prefix="/api/briefing", tags=["briefing"])

FREQ_MONTHLY = {"monthly": 1.0, "quarterly": 1 / 3, "half-yearly": 1 / 6, "yearly": 1 / 12}


def _due_on(day: int, y: int, m: int) -> date:
    day = max(1, min(int(day or 1), 31))
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day, last))


@router.get("")
def briefing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    uid = user.id
    today = ist.today()
    ym = today.strftime("%Y-%m")
    dim = calendar.monthrange(today.year, today.month)[1]
    days_left = dim - today.day + 1  # includes today

    # ---- spending this month ----
    exps = db.query(Expense).filter(Expense.user_id == uid,
                                    dialect.ym(Expense.txn_date) == ym).all()
    spent_month = sum(float(e.amount) for e in exps if e.kind == "expense")
    income_month = sum(float(e.amount) for e in exps if e.kind == "income")
    spent_today = sum(float(e.amount) for e in exps if e.kind == "expense" and e.txn_date == today)
    spent_before_today = spent_month - spent_today

    # ---- fixed monthly commitments: active loan EMIs + insurance (monthly-equiv) ----
    loans = db.query(Loan).filter(Loan.user_id == uid, Loan.status == "active").all()
    emi_total = sum(float(l.emi or 0) for l in loans)
    ins = db.query(Insurance).filter(Insurance.user_id == uid).all()
    ins_monthly = sum(float(p.premium or 0) * FREQ_MONTHLY.get((p.frequency or "yearly").lower(), 1 / 12) for p in ins)
    fixed = emi_total + ins_monthly

    savings_target = round(income_month * 0.20)
    discretionary = income_month - fixed - savings_target
    has_income = income_month > 0

    if discretionary > 0:
        allowance_today = max(0.0, (discretionary - spent_before_today) / days_left)
    else:
        allowance_today = 0.0
    remaining_today = allowance_today - spent_today
    ring_pct = min(1.0, spent_today / allowance_today) if allowance_today > 0 else (1.0 if spent_today > 0 else 0.0)

    month_over = max(0.0, spent_month - discretionary) if discretionary > 0 else spent_month
    safe = {
        "hasIncome": has_income,
        "remainingToday": round(remaining_today),
        "allowanceToday": round(allowance_today),
        "spentToday": round(spent_today),
        "overBudget": remaining_today < 0,
        "ringPct": round(ring_pct, 3),
        "savingsTarget": savings_target,
        "monthlyBudget": round(max(0.0, discretionary)),
        "spentMonth": round(spent_month),
        "monthOver": round(month_over),
    }

    # ---- bills due now (unpaid this month): cards + loan EMIs, one-tap payable ----
    paid_card_ids = {r.card_id for r in db.query(CardPayment.card_id)
                     .filter(CardPayment.user_id == uid, CardPayment.period == ym)}
    paid_loan_ids = {r.loan_id for r in db.query(LoanPayment.loan_id)
                     .filter(LoanPayment.user_id == uid, LoanPayment.period == ym)}
    bills = []
    for c in db.query(CreditCard).filter(CreditCard.user_id == uid):
        if c.id in paid_card_ids:
            continue
        day = int(c.billing_day or (c.due_date.day if c.due_date else 1))
        due = _due_on(day, today.year, today.month)
        bills.append({"type": "card", "id": c.id, "name": f"{c.bank or 'Card'} bill",
                      "sub": (c.last4 or ""), "amount": float(c.statement_amount) if c.statement_amount else None,
                      "due_fmt": due.strftime("%d-%m-%Y"), "days": (due - today).days})
    for l in loans:
        if l.id in paid_loan_ids:
            continue
        src = l.next_due_date or l.start_date
        day = src.day if src else 1
        due = _due_on(day, today.year, today.month)
        bills.append({"type": "loan", "id": l.id, "name": f"{l.lender or 'Loan'} EMI",
                      "sub": l.loan_type or "", "amount": float(l.emi) if l.emi else None,
                      "due_fmt": due.strftime("%d-%m-%Y"), "days": (due - today).days})
    bills.sort(key=lambda b: b["days"])
    bills_total = sum(b["amount"] for b in bills if b["amount"])

    # ---- unified DUES across every module (overdue + due within 30 days) ----
    WINDOW = 30
    dues = []
    for b in bills:  # cards + loan EMIs (already computed, all unpaid this month)
        dues.append({"module": "cards" if b["type"] == "card" else "loans", "kind": "bill",
                     "payType": b["type"], "id": b["id"], "title": b["name"], "sub": b["sub"],
                     "amount": b["amount"], "due_fmt": b["due_fmt"], "days": b["days"], "payable": True})
    for p in ins:  # insurance renewals coming up / overdue
        if p.renewal_date and (p.renewal_date - today).days <= WINDOW:
            d = (p.renewal_date - today).days
            dues.append({"module": "insurance", "kind": "renewal", "id": p.id,
                         "title": f"{p.provider or 'Policy'} · {p.policy_type or ''}".strip(" ·"),
                         "sub": "renewal", "amount": float(p.premium) if p.premium else None,
                         "due_fmt": p.renewal_date.strftime("%d-%m-%Y"), "days": d, "payable": False})
    for r in (db.query(Reminder).filter(Reminder.user_id == uid, Reminder.is_done == 0,
                                         Reminder.due_date != None).all()):  # noqa: E711
        if r.module_ref in ("cards", "loans"):  # avoid duplicating the actual bills
            continue
        d = (r.due_date - today).days
        if d <= WINDOW:
            dues.append({"module": r.module_ref or "reminders", "kind": "reminder", "id": r.id,
                         "title": r.title, "sub": "reminder", "amount": None,
                         "due_fmt": r.due_date.strftime("%d-%m-%Y"), "days": d, "payable": False})
    for t in (db.query(Todo).filter(Todo.user_id == uid, Todo.status == "pending",
                                    Todo.due_date != None).all()):  # noqa: E711
        d = (t.due_date - today).days
        if d <= WINDOW:
            dues.append({"module": "todo", "kind": "task", "id": t.id, "title": t.title,
                         "sub": t.priority or "task", "amount": None,
                         "due_fmt": t.due_date.strftime("%d-%m-%Y"), "days": d, "payable": False})
    dues.sort(key=lambda x: x["days"])

    # ---- logging streak (consecutive days with an expense, ending today or yesterday) ----
    rows = (db.query(Expense.txn_date).filter(Expense.user_id == uid, Expense.txn_date != None)  # noqa: E711
            .order_by(Expense.txn_date.desc()).limit(400).all())
    logged = {r[0] for r in rows if r[0]}
    today_logged = today in logged
    cursor = today if today_logged else today - timedelta(days=1)
    streak = 0
    while cursor in logged:
        streak += 1
        cursor -= timedelta(days=1)

    # ---- photo memory (on this day, previous years) ----
    md = today.strftime("%m-%d")
    mem = (db.query(GalleryPhoto)
           .filter(GalleryPhoto.user_id == uid, GalleryPhoto.is_trashed == 0,
                   dialect.md(GalleryPhoto.taken_at) == md,
                   dialect.year_of(GalleryPhoto.taken_at) < today.year)
           .order_by(GalleryPhoto.taken_at.desc()).first())
    memory = None
    if mem:
        yrs = today.year - mem.taken_at.year
        memory = {"thumb_url": media_url(mem.user_id, THUMB, mem.filename), "years": yrs,
                  "label": f"{yrs} year{'s' if yrs != 1 else ''} ago today"}

    return {
        "date": today.strftime("%A, %d %B"),
        "safeToSpend": safe,
        "spentToday": round(spent_today),
        "incomeMonth": round(income_month),
        "bills": bills[:6],
        "billsTotal": round(bills_total),
        "billsCount": len(bills),
        "dues": dues[:25],
        "duesCount": len(dues),
        "streak": {"current": streak, "todayLogged": today_logged},
        "memory": memory,
    }
