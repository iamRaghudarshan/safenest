"""Builds the daily "what needs you today" summary used for push notifications.

Deliberately separate from the briefing endpoint: a notification has a few dozen
characters of attention, so this collapses everything into one headline plus a
short body rather than the full feed the dashboard renders.
"""
import calendar
from datetime import date, timedelta

from sqlalchemy.orm import Session

from . import ist
from .models import (CardPayment, CreditCard, Document, Insurance, Loan, LoanPayment,
                     NotificationPref, Reminder, Todo)

SOON_DAYS = 30  # how far ahead a renewal/expiry counts as "coming up"


def _due_on(day: int, y: int, m: int) -> date:
    day = max(1, min(int(day or 1), 31))
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _money(n) -> str:
    """Compact rupee formatting — notifications have very little room."""
    v = float(n or 0)
    if v >= 1e7:
        return f"₹{v / 1e7:.2f}Cr".replace(".00", "")
    if v >= 1e5:
        return f"₹{v / 1e5:.2f}L".replace(".00", "")
    if v >= 1000:
        return f"₹{v / 1000:.1f}k".replace(".0", "")
    return f"₹{int(v)}"


def build(db: Session, user_id: int, pref: NotificationPref | None = None) -> dict | None:
    """Return {title, body, url, count} for today, or None when there's nothing
    worth interrupting the user for."""
    today = ist.today()
    ym = today.strftime("%Y-%m")
    want_bills = not pref or pref.include_bills
    want_reminders = not pref or pref.include_reminders
    want_expiry = not pref or pref.include_expiry

    overdue: list[str] = []   # already past due — lead with these
    due_today: list[str] = []
    soon: list[str] = []
    count = 0

    if want_bills:
        paid_cards = {r.card_id for r in db.query(CardPayment.card_id)
                      .filter(CardPayment.user_id == user_id, CardPayment.period == ym)}
        for c in db.query(CreditCard).filter(CreditCard.user_id == user_id):
            if c.id in paid_cards:
                continue
            day = int(c.billing_day or (c.due_date.day if c.due_date else 1))
            days = (_due_on(day, today.year, today.month) - today).days
            label = f"{c.bank or 'Card'} bill"
            if c.statement_amount:
                label += f" {_money(c.statement_amount)}"
            count += 1
            (overdue if days < 0 else due_today if days == 0 else soon).append(label)

        paid_loans = {r.loan_id for r in db.query(LoanPayment.loan_id)
                      .filter(LoanPayment.user_id == user_id, LoanPayment.period == ym)}
        for l in db.query(Loan).filter(Loan.user_id == user_id, Loan.status == "active"):
            if l.id in paid_loans:
                continue
            src = l.next_due_date or l.start_date
            days = (_due_on(src.day if src else 1, today.year, today.month) - today).days
            label = f"{l.lender or 'Loan'} EMI"
            if l.emi:
                label += f" {_money(l.emi)}"
            count += 1
            (overdue if days < 0 else due_today if days == 0 else soon).append(label)

    if want_reminders:
        for r in db.query(Reminder).filter(Reminder.user_id == user_id, Reminder.is_done == 0,
                                           Reminder.due_date.isnot(None)):
            days = (r.due_date - today).days
            if days > SOON_DAYS:
                continue
            count += 1
            (overdue if days < 0 else due_today if days == 0 else soon).append(r.title or "Reminder")
        for t in db.query(Todo).filter(Todo.user_id == user_id, Todo.status == "pending",
                                       Todo.due_date.isnot(None)):
            days = (t.due_date - today).days
            if days > SOON_DAYS:
                continue
            count += 1
            (overdue if days < 0 else due_today if days == 0 else soon).append(t.title or "Task")

    if want_expiry:
        horizon = today + timedelta(days=SOON_DAYS)
        for p in db.query(Insurance).filter(Insurance.user_id == user_id,
                                            Insurance.renewal_date.isnot(None),
                                            Insurance.renewal_date <= horizon):
            days = (p.renewal_date - today).days
            count += 1
            label = f"{p.provider or 'Policy'} renewal"
            (overdue if days < 0 else due_today if days == 0 else soon).append(label)
        for d in db.query(Document).filter(Document.user_id == user_id,
                                           Document.is_trashed == 0,
                                           Document.expiry_date.isnot(None),
                                           Document.expiry_date <= horizon):
            days = (d.expiry_date - today).days
            count += 1
            label = f"{d.title} expires" if days >= 0 else f"{d.title} expired"
            (overdue if days < 0 else due_today if days == 0 else soon).append(label)

    if not count:
        return None

    # Headline reflects the most urgent bucket; the body names a few specifics so
    # the notification is useful without opening the app.
    if overdue:
        title = f"{len(overdue)} overdue" + (f" · {len(due_today)} due today" if due_today else "")
        items = overdue + due_today + soon
    elif due_today:
        title = f"{len(due_today)} due today"
        items = due_today + soon
    else:
        title = f"{len(soon)} coming up"
        items = soon

    shown = items[:3]
    body = " · ".join(shown)
    if len(items) > len(shown):
        body += f" · +{len(items) - len(shown)} more"

    # The app's own name, so a renamed copy does not push notifications
    # branded with whatever it used to be called.
    from .routers.branding import current as _branding
    name = _branding(db)["app_name"]
    return {"title": f"{name} · {title}", "body": body, "url": "/", "count": count}
