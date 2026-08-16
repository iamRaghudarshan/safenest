"""Support tickets.

Because every customer copy runs its own isolated server, a ticket raised inside a
customer's app would live on THEIR machine, not reach the publisher. So the
customer channel is the WEBSITE (which hits the publisher's server) — the same
place they request a licence — and the publisher manages every ticket here. Email
notifications (admin on a new ticket, customer on a reply) ride the mail queue.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import ist, licensing, mailer
from ..database import get_db
from ..helpers import audit
from ..models import Ticket, TicketMessage, User
from ..ratelimit import rate_limit
from ..security import require_admin
from .branding import app_name
from .licences import _require_publisher

admin_t = APIRouter(prefix="/api/admin/tickets", tags=["tickets"])
public_t = APIRouter(prefix="/api/public", tags=["support"])


def _row(t: Ticket, messages=None) -> dict:
    d = {"id": t.id, "subject": t.subject, "status": t.status, "priority": t.priority,
         "name": t.name, "email": t.email, "source": t.source, "licence_key": t.licence_key,
         "created_at": ist.fmt(t.created_at), "updated_at": ist.fmt(t.updated_at)}
    if messages is not None:
        d["messages"] = [{"id": m.id, "author": m.author, "author_name": m.author_name,
                          "body": m.body, "at": ist.fmt(m.created_at)} for m in messages]
    return d


def _notify_admin(db: Session, t: Ticket, body: str) -> None:
    m = mailer.settings_row(db)
    if mailer.is_configured(db) and m and m.from_addr:
        mailer.enqueue(db, m.from_addr, f"Support: {t.subject}",
            f"{t.name} <{t.email}> ({t.source}):\n\n{body}\n\n"
            f"Reply in the app under Administration -> Support.", kind="ticket")


def _notify_customer(db: Session, t: Ticket, body: str) -> None:
    if mailer.is_configured(db) and t.email:
        mailer.enqueue(db, t.email, f"Re: {t.subject} - {app_name(db)} support",
            f"Hi {t.name},\n\n{body}\n\n- {app_name(db)} support", kind="ticket")


# ------------------------------------------------------------- customer (website)
@public_t.post("/support")
def web_support(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """A support request from the website. Open to anyone; the email identifies them
    and a licence key (if given) ties it to a customer."""
    rate_limit(request, "web-support", limit=5, window=3600)
    name = (body.get("name") or "").strip()[:120]
    email = (body.get("email") or "").strip().lower()[:160]
    subject = (body.get("subject") or "").strip()[:200]
    text = (body.get("body") or "").strip()[:5000]
    if not name or not licensing.looks_like_email(email) or len(subject) < 3 or len(text) < 5:
        raise HTTPException(422, "Name, a valid email, a subject and a message are required.")
    now = ist.now()
    key = (body.get("licence_key") or "").strip()[:16] or None
    t = Ticket(user_id=None, name=name, email=email, subject=subject, status="open",
               priority="normal", licence_key=key, source="web",
               created_at=now, updated_at=now)
    db.add(t); db.flush()
    db.add(TicketMessage(ticket_id=t.id, author="customer", author_name=name, body=text, created_at=now))
    db.commit(); db.refresh(t)
    _notify_admin(db, t, text)
    return {"ok": True}


# ------------------------------------------------------------------------- admin
@admin_t.get("")
def all_tickets(status: str = "", admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    q = db.query(Ticket).order_by(Ticket.updated_at.desc())
    if status:
        q = q.filter(Ticket.status == status[:12])
    rows = q.limit(200).all()
    return {"tickets": [_row(t) for t in rows],
            "open": db.query(Ticket).filter(Ticket.status != "closed").count()}


@admin_t.get("/{tid}")
def admin_ticket(tid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    t = db.query(Ticket).get(tid)
    if not t:
        raise HTTPException(404, "Ticket not found")
    msgs = db.query(TicketMessage).filter(TicketMessage.ticket_id == tid).order_by(TicketMessage.id.asc()).all()
    return _row(t, msgs)


@admin_t.post("/{tid}/reply")
def admin_reply(tid: int, request: Request, body: dict = Body(...),
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    t = db.query(Ticket).get(tid)
    if not t:
        raise HTTPException(404, "Ticket not found")
    text = (body.get("body") or "").strip()[:5000]
    if len(text) < 1:
        raise HTTPException(422, "Write a reply")
    now = ist.now()
    db.add(TicketMessage(ticket_id=t.id, author="admin",
                         author_name=admin.name or "Support", body=text, created_at=now))
    t.status = body.get("status") if body.get("status") in ("open", "pending", "closed") else "pending"
    t.updated_at = now
    db.commit()
    _notify_customer(db, t, text)
    audit(db, admin.id, "ticket_reply", "ticket", t.id, {"label": t.subject}, request=request)
    return {"ok": True}


@admin_t.post("/{tid}/status")
def set_status(tid: int, body: dict = Body(...),
               admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    t = db.query(Ticket).get(tid)
    if not t:
        raise HTTPException(404, "Ticket not found")
    if body.get("status") in ("open", "pending", "closed"):
        t.status = body["status"]
    if body.get("priority") in ("low", "normal", "high"):
        t.priority = body["priority"]
    t.updated_at = ist.now()
    db.commit()
    return _row(t)
