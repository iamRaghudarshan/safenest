"""Web Push delivery.

Sending is best-effort: a device that has uninstalled the app or revoked
permission returns 404/410, and that subscription is dropped so the list never
accumulates dead endpoints.
"""
import json
from datetime import datetime

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from . import ist
from .config import settings
from .models import Notification, PushSubscription


def _vapid_claims() -> dict:
    return {"sub": settings.vapid_subject}


def send_to_subscription(sub: PushSubscription, payload: dict) -> tuple[bool, int | None]:
    """Deliver one payload. Returns (ok, http_status).

    The status is the PUSH SERVICE's, not the phone's. 201 means Apple/Google took
    custody of the message — it says nothing about whether the device displayed it.
    """
    try:
        response = webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims=_vapid_claims(),
            timeout=10,
        )
        # The push service ACCEPTED it (201 for Apple/Google). That is not a promise
        # the phone will show it: iOS drops pushes silently when the permission has
        # been revoked, and the sender is never told. Hence the notifications table.
        return True, getattr(response, "status_code", 201)
    except WebPushException as e:
        return False, getattr(e.response, "status_code", None)
    except Exception:
        # Malformed or truncated keys raise ValueError from the crypto layer rather
        # than WebPushException. Such a subscription can never be delivered to, so
        # report it as Gone and let the caller prune it — one corrupt row must not
        # abort the whole send, or a scheduler pass.
        return False, 410


def notify(db: Session, user_id: int, title: str, body: str,
           url: str = "/", kind: str = "system", tag: str | None = None) -> dict:
    """Record a notification in the app AND try to push it to the user's devices.

    Everything user-facing should go through here rather than send_to_user: the
    stored row is what makes the bell icon truthful when a push is dropped by the
    OS, arrives while the phone is off, or was never permitted in the first place.
    """
    row = Notification(user_id=user_id, kind=kind, title=title[:160], body=body,
                       url=url[:255], is_read=0, pushed=0, created_at=ist.now())
    db.add(row)
    db.commit()
    db.refresh(row)

    result = send_to_user(db, user_id, {
        "title": title, "body": body, "url": url, "tag": tag or f"finmate-{kind}",
    })
    if result.get("sent"):
        row.pushed = 1
        db.commit()
    return {"id": row.id, **result}


def send_to_user(db: Session, user_id: int, payload: dict) -> dict:
    """Push to every device a user has registered. Prunes dead subscriptions."""
    if not settings.push_enabled:
        return {"sent": 0, "failed": 0, "removed": 0, "reason": "push not configured"}

    subs = db.query(PushSubscription).filter(PushSubscription.user_id == user_id).all()
    sent = failed = removed = 0
    now = ist.now()
    for sub in subs:
        ok, status = send_to_subscription(sub, payload)
        if ok:
            sub.last_sent_at = now
            sent += 1
        elif status in (404, 410):
            # Gone for good: the browser dropped the subscription.
            db.delete(sub)
            removed += 1
        else:
            failed += 1
    db.commit()
    return {"sent": sent, "failed": failed, "removed": removed, "devices": len(subs)}
