"""Admin SMTP settings — configure how the publisher emails customers.

Publisher + admin only (registered behind settings.is_publisher in main.py). The
password is stored AES-encrypted (crypto.py) and never returned to the browser.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import crypto, ist, mailer
from ..database import get_db
from ..helpers import audit
from ..models import MailSettings, User
from ..security import require_admin
from .licences import _require_publisher

router = APIRouter(prefix="/api/admin/mail", tags=["mail"])


def _row(m: MailSettings | None) -> dict:
    return {
        "enabled": bool(m and m.enabled),
        "host": (m.host if m else "") or "",
        "port": (m.port if m else 587) or 587,
        "username": (m.username if m else "") or "",
        "from_addr": (m.from_addr if m else "") or "",
        "from_name": (m.from_name if m else "") or "",
        "security": (m.security if m else "tls") or "tls",
        # Never send the password back — only whether one is stored.
        "has_password": bool(m and m.password_enc),
    }


@router.get("")
def get_mail(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    return _row(db.query(MailSettings).filter(MailSettings.id == 1).first())


@router.put("")
def set_mail(request: Request, body: dict = Body(...),
             admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    m = db.query(MailSettings).filter(MailSettings.id == 1).first()
    if not m:
        m = MailSettings(id=1)
        db.add(m)
    m.host = (body.get("host") or "").strip()[:255]
    try:
        m.port = int(body.get("port") or 587)
    except (TypeError, ValueError):
        raise HTTPException(422, "Port must be a number")
    m.username = (body.get("username") or "").strip()[:255]
    m.from_addr = (body.get("from_addr") or "").strip()[:255]
    m.from_name = (body.get("from_name") or "").strip()[:120]
    m.security = body.get("security") if body.get("security") in ("tls", "ssl", "none") else "tls"
    m.enabled = 1 if body.get("enabled") else 0
    # Only overwrite the password when a new one is supplied — a blank field means
    # "keep the stored one", since the browser is never given it to round-trip.
    pw = body.get("password")
    if pw:
        m.password_enc = crypto.encrypt(pw)
    if m.enabled and not (m.host and m.from_addr):
        raise HTTPException(422, "A host and a 'from' address are required to enable email")
    m.updated_at = ist.now()
    db.commit()
    audit(db, admin.id, "mail_config", "settings", 1,
          {"label": "Email settings", "host": m.host, "enabled": bool(m.enabled)}, request=request)
    return _row(m)


@router.get("/log")
def mail_log(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """The send queue + history: what was sent, queued, or failed and why."""
    _require_publisher()
    from ..models import MailLog
    rows = db.query(MailLog).order_by(MailLog.id.desc()).limit(60).all()
    queued = db.query(MailLog).filter(MailLog.status == "queued").count()
    return {"queued": queued, "items": [{
        "id": r.id, "to": r.to_addr, "subject": r.subject, "kind": r.kind,
        "status": r.status, "error": r.error, "at": ist.fmt(r.created_at),
        "sent_at": ist.fmt(r.sent_at)} for r in rows]}


@router.post("/test")
def test_mail(body: dict = Body(default={}), admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    _require_publisher()
    to = (body.get("to") or admin.email or "").strip()
    if not to:
        raise HTTPException(422, "Enter an address to send the test to")
    ok, msg = mailer.test(db, to)
    if not ok:
        raise HTTPException(400, f"Could not send: {msg}")
    return {"ok": True, "sent_to": to}
