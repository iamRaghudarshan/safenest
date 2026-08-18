from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist
from .. import dialect
from ..database import get_db
from ..models import (CardPayment, CreditCard, Document, Expense, GalleryPhoto, Habit, HabitLog, Insurance,
                      Investment, Loan, LoanPayment, Reminder, Todo, User, VaultItem)
from ..security import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    uid = user.id
    ym = ist.today().strftime("%Y-%m")

    exps = db.query(Expense).filter(Expense.user_id == uid,
                                    dialect.ym(Expense.txn_date) == ym).all()
    income = sum(float(e.amount) for e in exps if e.kind == "income")
    spend = sum(float(e.amount) for e in exps if e.kind == "expense")

    inv = db.query(Investment).filter(Investment.user_id == uid).all()
    inv_val = sum(float(i.current_value) for i in inv)
    inv_cost = sum(float(i.invested_amount) for i in inv)
    inv_delta = round((inv_val - inv_cost) / inv_cost * 100, 1) if inv_cost else 0

    loans = db.query(Loan).filter(Loan.user_id == uid, Loan.status == "active").all()
    outstanding = sum(float(l.outstanding) for l in loans)

    rem = db.query(Reminder).filter(Reminder.user_id == uid, Reminder.is_done == 0).order_by(
        Reminder.due_date.asc()).limit(15).all()
    today = ist.today()
    upcoming, dues = [], 0
    for r in rem:
        days = (r.due_date - today).days if r.due_date else None
        if days is not None and days <= 7:
            dues += 1
        upcoming.append({"title": r.title, "module": r.module_ref,
                         "due": r.due_date.strftime("%d-%m-%Y") if r.due_date else None, "days": days})

    def count(model, *filters):
        q = db.query(model).filter(model.user_id == uid)
        for f in filters:
            q = q.filter(f)
        return q.count()

    # ---- attention: things that need action now (unpaid bills / overdue) ----
    paid_card_ids = {r.card_id for r in db.query(CardPayment.card_id)
                     .filter(CardPayment.user_id == uid, CardPayment.period == ym)}
    cards_unpaid = sum(1 for c in db.query(CreditCard.id).filter(CreditCard.user_id == uid)
                       if c.id not in paid_card_ids)
    paid_loan_ids = {r.loan_id for r in db.query(LoanPayment.loan_id)
                     .filter(LoanPayment.user_id == uid, LoanPayment.period == ym)}
    loans_unpaid = sum(1 for l in loans if l.id not in paid_loan_ids)  # active loans only
    rem_overdue = count(Reminder, Reminder.is_done == 0, Reminder.due_date != None, Reminder.due_date <= today)  # noqa: E711
    todo_overdue = count(Todo, Todo.status == "pending", Todo.due_date != None, Todo.due_date < today)  # noqa: E711
    ins_expired = count(Insurance, Insurance.renewal_date != None, Insurance.renewal_date < today)  # noqa: E711

    # Habits still to do today: active (non-archived) habits whose goal applies
    # today and whose logged total has not yet reached the target. Reuses the
    # router's own weekday logic so the badge agrees with the module.
    from .habits import _active_on
    active_habits = db.query(Habit).filter(Habit.user_id == uid, Habit.archived == 0).all()
    today_done = {r.habit_id: (r.count or 0) for r in db.query(HabitLog)
                  .filter(HabitLog.user_id == uid, HabitLog.log_date == today).all()}
    habits_todo = sum(1 for h in active_habits if _active_on(h, today)
                      and today_done.get(h.id, 0) < max(1, h.target_count or 1))

    return {
        "stats": {"investValue": inv_val, "investDelta": inv_delta, "monthSpend": spend,
                  "monthIncome": income, "outstanding": outstanding, "duesCount": dues},
        "moduleTotals": {
            "loans": len(loans), "loansValue": outstanding,
            "cards": count(CreditCard), "insurance": count(Insurance),
            "investments": len(inv), "investValue": inv_val, "investDelta": inv_delta,
            "expenses": spend,
            "reminders": count(Reminder, Reminder.is_done == 0),
            "todo": count(Todo, Todo.status == "pending"),
            "habits": count(Habit, Habit.archived == 0),
            "gallery": count(GalleryPhoto, GalleryPhoto.is_trashed == 0),
            "vault": count(VaultItem),
            "documents": count(Document, Document.is_trashed == 0),
        },
        "moduleAttention": {
            "cards": cards_unpaid,
            "loans": loans_unpaid,
            "reminders": rem_overdue,
            "todo": todo_overdue,
            "habits": habits_todo,
            "insurance": ins_expired,
            "documents": count(Document, Document.is_trashed == 0,
                               Document.expiry_date != None,  # noqa: E711
                               Document.expiry_date < today + timedelta(days=30)),
        },
        "upcoming": upcoming,
    }
