"""Emailing customers — licence keys on approval, and announcements/ads.

Publisher-side only. Uses the SMTP settings an admin configures (the mail_settings
row); the password is AES-decrypted from crypto.py at send time and never logged.
Delivery is best-effort: an email failure must never break issuing a licence or
posting a broadcast.
"""
import smtplib
import threading
import time
from email.message import EmailMessage
from html import escape as _esc

from . import crypto, ist
from .database import SessionLocal
from .models import MailLog, MailSettings

_wake = threading.Event()


def settings_row(db):
    return db.query(MailSettings).filter(MailSettings.id == 1).first()


def is_configured(db) -> bool:
    m = settings_row(db)
    return bool(m and m.enabled and m.host and m.from_addr)


def _password(m) -> str:
    if not m.password_enc:
        return ""
    try:
        return crypto.decrypt(m.password_enc)
    except Exception:
        return ""


def _connect(m):
    sec = (m.security or "tls").lower()
    if sec == "ssl":
        srv = smtplib.SMTP_SSL(m.host, int(m.port or 465), timeout=20)
    else:
        srv = smtplib.SMTP(m.host, int(m.port or 587), timeout=20)
        if sec == "tls":
            srv.starttls()
    if m.username:
        srv.login(m.username, _password(m))
    return srv


def send(db, to: str, subject: str, body: str, html: str | None = None) -> tuple[bool, str]:
    """Send one email. Returns (ok, message). Never raises."""
    m = settings_row(db)
    if not (m and m.host and m.from_addr):
        return False, "Email is not configured."
    if not (to or "").strip():
        return False, "No recipient."
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{m.from_name} <{m.from_addr}>" if m.from_name else m.from_addr
    msg["To"] = to.strip()
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        srv = _connect(m)
        try:
            srv.send_message(msg)
        finally:
            try:
                srv.quit()
            except Exception:
                pass
        return True, "sent"
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"


def test(db, to: str) -> tuple[bool, str]:
    from .routers.branding import app_name
    name = app_name(db)
    return send(db, to, f"{name} - test email",
                f"This is a test email from {name}. If you can read this, your "
                f"SMTP settings are working.")


# ------------------------------------------------------------- branded template
def alert_html(db, *, title: str, intro: str,
               rows: list[tuple[str, str]] | None = None,
               items: list[str] | None = None,
               footer: str | None = None,
               accent: str = "#0176D3", emoji: str = "🔔") -> str:
    """A branded, COLOURFUL single-column HTML email for an alert.

    Inline styles only — email clients strip <style> and external CSS, so every
    rule lives on the element. A brand-gradient header carries the app name and an
    emoji, a thin `accent` strip gives the alert its own colour, and the message
    sits in a white card: values become tinted pills and a list gets accent
    bullets. Gradients degrade to a solid colour in Outlook; everything else is
    plain tables, so it renders the same in Gmail, Apple Mail and iOS."""
    from .routers.branding import app_name
    name = app_name(db)
    b1, b2 = "#0176D3", "#1B96FF"            # brand gradient
    pill_bg = "#eef2f8"                       # safe neutral pill fill

    rows_html = ""
    if rows:
        cells = "".join(
            f'<tr>'
            f'<td style="padding:7px 0;color:#5a5d78;font-size:13px">{_esc(k)}</td>'
            f'<td align="right" style="padding:7px 0">'
            f'<span style="background:{pill_bg};color:{accent};font-weight:700;'
            f'font-size:13px;padding:5px 13px;border-radius:999px;'
            f'white-space:nowrap">{_esc(v)}</span></td></tr>'
            for k, v in rows)
        rows_html = ('<table width="100%" style="margin-top:16px;'
                     f'border-collapse:collapse">{cells}</table>')

    items_html = ""
    if items:
        lis = "".join(
            f'<tr>'
            f'<td width="20" valign="top" style="color:{accent};font-size:17px;'
            f'font-weight:900;padding:8px 0;line-height:1.2">&bull;</td>'
            f'<td style="padding:8px 0;color:#1a1a2e;font-size:14px;'
            f'border-bottom:1px solid #eef1f6">{_esc(x)}</td></tr>'
            for x in items)
        items_html = ('<table width="100%" style="margin-top:12px;'
                      f'border-collapse:collapse">{lis}</table>')

    foot = _esc(footer) if footer else f"Sent by {_esc(name)} from your own computer."
    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:#eef1f6;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#eef1f6;padding:24px 12px"><tr><td align="center">'
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:480px;background:#ffffff;border-radius:18px;'
        'overflow:hidden;box-shadow:0 8px 28px rgba(20,30,60,0.10)">'
        # Brand gradient header (solid fallback for Outlook) with an emoji.
        f'<tr><td style="background:{b1};'
        f'background:linear-gradient(135deg,{b1},{b2});padding:20px 22px">'
        f'<span style="font-size:22px;vertical-align:middle">{emoji}</span>'
        f'<span style="color:#ffffff;font-size:18px;font-weight:800;'
        f'letter-spacing:-0.3px;margin-left:9px;vertical-align:middle">{_esc(name)}</span>'
        '</td></tr>'
        # The alert's own colour, a thin strip.
        f'<tr><td style="height:5px;background:{accent};line-height:5px;'
        'font-size:0">&nbsp;</td></tr>'
        '<tr><td style="padding:24px 22px 8px">'
        f'<div style="font-size:20px;font-weight:800;color:{accent};'
        f'margin-bottom:6px">{_esc(title)}</div>'
        f'<div style="font-size:14px;color:#34364e;line-height:1.5">{_esc(intro)}</div>'
        f'{rows_html}{items_html}</td></tr>'
        '<tr><td style="padding:18px 22px 24px;color:#8a8da3;font-size:12px;'
        f'line-height:1.5">{foot}</td></tr>'
        '</table></td></tr></table></body></html>')


# ---------------------------------------------------------------- queued sending
def enqueue(db, to: str, subject: str, body: str, kind: str = "broadcast",
            html: str | None = None) -> int:
    """Add one email to the queue (a mail_log row). A background worker sends it
    one at a time, so a bulk send never blocks the request that started it. An
    optional HTML part rides alongside the plain-text body."""
    row = MailLog(to_addr=(to or "").strip()[:255], subject=(subject or "")[:255],
                  body=body or "", html=html, kind=kind, status="queued",
                  attempts=0, created_at=ist.now())
    db.add(row)
    db.commit()
    _wake.set()
    return row.id


def _worker_loop() -> None:
    """Drain the queue, one email at a time, recording each outcome."""
    while True:
        try:
            db = SessionLocal()
            try:
                if not is_configured(db):
                    _wake.wait(timeout=60); _wake.clear(); continue
                pending = (db.query(MailLog).filter(MailLog.status == "queued")
                           .order_by(MailLog.id.asc()).limit(100).all())
                if not pending:
                    _wake.wait(timeout=30); _wake.clear(); continue
                for row in pending:
                    ok, msg = send(db, row.to_addr, row.subject, row.body,
                                   html=row.html)
                    row.attempts = (row.attempts or 0) + 1
                    row.status = "sent" if ok else "failed"
                    row.error = None if ok else msg[:300]
                    row.sent_at = ist.now()
                    db.commit()
                    time.sleep(0.4)      # a gentle, one-by-one pace
            finally:
                db.close()
        except Exception as exc:         # a worker must never die on one bad send
            print(f"[mailer] worker error: {exc}")
            time.sleep(10)


def start_worker() -> None:
    threading.Thread(target=_worker_loop, daemon=True, name="mailer").start()
