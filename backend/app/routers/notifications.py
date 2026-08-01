"""Push-notification subscriptions and per-user delivery settings."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import ist
from .. import digest, push
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import Notification, NotificationPref, PushSubscription, User
from ..ratelimit import rate_limit
from ..security import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def get_pref(db: Session, user_id: int) -> NotificationPref:
    pref = db.query(NotificationPref).get(user_id)
    if not pref:
        pref = NotificationPref(user_id=user_id, enabled=0, send_hour=9, send_minute=0,
                                include_bills=1, include_reminders=1, include_expiry=1,
                                updated_at=ist.now())
        db.add(pref); db.commit(); db.refresh(pref)
    return pref


def _present(pref: NotificationPref, devices: int) -> dict:
    return {
        "available": settings.push_enabled,
        "publicKey": settings.vapid_public_key or None,
        "enabled": bool(pref.enabled),
        "sendHour": int(pref.send_hour or 9),
        "sendMinute": int(pref.send_minute or 0),
        "includeBills": bool(pref.include_bills),
        "includeReminders": bool(pref.include_reminders),
        "includeExpiry": bool(pref.include_expiry),
        "devices": devices,
        "lastSentOn": pref.last_sent_on.isoformat() if pref.last_sent_on else None,
    }


@router.get("/settings")
def read_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pref = get_pref(db, user.id)
    devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    return _present(pref, devices)


@router.put("/settings")
def write_settings(body: dict = Body(...), user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    pref = get_pref(db, user.id)
    if "enabled" in body:
        pref.enabled = 1 if body["enabled"] else 0
    if "sendHour" in body:
        try:
            h = int(body["sendHour"])
        except (TypeError, ValueError):
            raise HTTPException(422, "Send hour must be a number")
        if not 0 <= h <= 23:
            raise HTTPException(422, "Send hour must be between 0 and 23")
        pref.send_hour = h
    if "sendMinute" in body:
        try:
            m = int(body["sendMinute"])
        except (TypeError, ValueError):
            raise HTTPException(422, "Send minute must be a number")
        if not 0 <= m <= 59:
            raise HTTPException(422, "Send minute must be between 0 and 59")
        pref.send_minute = m
    for key, col in (("includeBills", "include_bills"), ("includeReminders", "include_reminders"),
                     ("includeExpiry", "include_expiry")):
        if key in body:
            setattr(pref, col, 1 if body[key] else 0)
    pref.updated_at = ist.now()
    db.commit()
    devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    return _present(pref, devices)


@router.post("/subscribe")
def subscribe(body: dict = Body(...), request: Request = None,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Register this device. The browser hands us an endpoint plus the keys used
    to encrypt payloads for it."""
    if not settings.push_enabled:
        raise HTTPException(501, "Push notifications aren’t configured on the server")
    endpoint = (body.get("endpoint") or "").strip()
    keys = body.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(422, "Incomplete push subscription")

    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if sub:
        # Same device re-subscribing (possibly as a different user) — retarget it.
        sub.user_id = user.id
        sub.p256dh = keys["p256dh"]
        sub.auth = keys["auth"]
    else:
        sub = PushSubscription(
            user_id=user.id, endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"],
            user_agent=(request.headers.get("user-agent") or "")[:255] if request else None,
            created_at=ist.now(),
        )
        db.add(sub)
    pref = get_pref(db, user.id)
    pref.enabled = 1  # opting in on a device turns the digest on
    db.commit()
    audit(db, user.id, "push_subscribe", "user", user.id, request=request)
    devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    return _present(pref, devices)


@router.post("/unsubscribe")
def unsubscribe(body: dict = Body(default={}), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """Drop one device (by endpoint), or every device for this user."""
    endpoint = (body.get("endpoint") or "").strip()
    q = db.query(PushSubscription).filter(PushSubscription.user_id == user.id)
    if endpoint:
        q = q.filter(PushSubscription.endpoint == endpoint)
    removed = q.delete(synchronize_session=False)
    pref = get_pref(db, user.id)
    left = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    if not left:
        pref.enabled = 0
    db.commit()
    return _present(pref, left) | {"removed": removed}


@router.post("/test")
def send_test(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send the digest immediately so the user can confirm it arrives."""
    rate_limit(request, f"push-test:{user.id}", limit=5, window=300)
    devices = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).count()
    if not devices:
        raise HTTPException(422, "No device is set up for notifications yet")
    pref = get_pref(db, user.id)
    from .branding import app_name
    payload = digest.build(db, user.id, pref) or {
        "title": app_name(db), "body": "Nothing is due right now — you’re all caught up.",
        "url": "/", "count": 0,
    }
    result = push.notify(db, user.id, payload["title"], payload["body"],
                         payload.get("url", "/"), kind="digest")
    return {"preview": payload, **result}


# ---------------------------------------------------------------- inbox
# The in-app notification list. A push can be dropped by the operating system
# without the server ever knowing, so this — not the push — is the record the
# bell icon reads from.

def _item(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "url": n.url or "/",
        "read": bool(n.is_read),
        "pushed": bool(n.pushed),
        "at": n.created_at.isoformat(timespec="seconds") if n.created_at else None,
    }


@router.get("/inbox")
def inbox(offset: int = 0, limit: int = 30,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    offset, limit = max(0, offset), min(max(1, limit), 100)
    q = db.query(Notification).filter(Notification.user_id == user.id)
    total = q.count()
    unread = q.filter(Notification.is_read == 0).count()
    rows = q.order_by(Notification.id.desc()).offset(offset).limit(limit).all()
    return {"items": [_item(n) for n in rows], "total": total, "unread": unread,
            "offset": offset, "limit": limit}


@router.get("/unread")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cheap enough to poll for the badge."""
    n = (db.query(Notification)
         .filter(Notification.user_id == user.id, Notification.is_read == 0).count())
    return {"unread": n}


@router.post("/inbox/{id}/read")
def mark_read(id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = (db.query(Notification)
         .filter(Notification.id == id, Notification.user_id == user.id).first())
    if not n:
        raise HTTPException(404, "Notification not found")
    n.is_read = 1
    db.commit()
    return {"id": id, "read": True}


@router.post("/inbox/read-all")
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = (db.query(Notification)
         .filter(Notification.user_id == user.id, Notification.is_read == 0)
         .update({Notification.is_read: 1}, synchronize_session=False))
    db.commit()
    return {"read": int(n)}


@router.delete("/inbox/{id}")
def delete_one(id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = (db.query(Notification)
         .filter(Notification.id == id, Notification.user_id == user.id).first())
    if not n:
        raise HTTPException(404, "Notification not found")
    db.delete(n)
    db.commit()
    return {"deleted": id}


@router.delete("/inbox")
def clear_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = (db.query(Notification).filter(Notification.user_id == user.id)
         .delete(synchronize_session=False))
    db.commit()
    return {"deleted": int(n)}
