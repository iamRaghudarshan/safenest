"""Daily digest scheduler.

A single daemon thread wakes every minute and sends to any user whose configured
time has arrived and who hasn't already been sent to today. A plain thread rather
than APScheduler/cron: this is one job on a single-instance app, and it must not
add an operational dependency to something the user runs by double-clicking.

Because the server is a PC that may be off, `last_sent_on` is a DATE rather than a
timestamp — if the machine was asleep at the send time and wakes later the same
day, the digest still goes out once, late, instead of being skipped entirely.
"""
import threading
import time
from datetime import date, datetime

from . import ist
from . import digest, push
from .config import settings
from .database import SessionLocal
from .models import NotificationPref, PushSubscription, User

CHECK_SECONDS = 60
_started = False


def _due_now(pref: NotificationPref, now: datetime) -> bool:
    if not pref.enabled:
        return False
    if pref.last_sent_on == now.date():
        return False  # already sent today
    target = now.replace(hour=int(pref.send_hour or 9), minute=int(pref.send_minute or 0),
                         second=0, microsecond=0)
    return now >= target


def run_once(now: datetime | None = None) -> dict:
    """One pass over all users. Returns a small summary (used by tests too)."""
    now = now or ist.now()
    summary = {"checked": 0, "sent": 0, "skipped_empty": 0, "no_devices": 0}
    db = SessionLocal()
    try:
        prefs = db.query(NotificationPref).filter(NotificationPref.enabled == 1).all()
        for pref in prefs:
            summary["checked"] += 1
            if not _due_now(pref, now):
                continue
            user = db.query(User).get(pref.user_id)
            if not user or user.status != "active":
                continue
            if not db.query(PushSubscription).filter(
                    PushSubscription.user_id == pref.user_id).count():
                summary["no_devices"] += 1
                continue

            payload = digest.build(db, pref.user_id, pref)
            # Nothing due is good news — don't interrupt anyone to say so, but do
            # mark the day as handled so we don't retry every minute.
            if not payload:
                pref.last_sent_on = now.date()
                db.commit()
                summary["skipped_empty"] += 1
                continue

            # notify() writes the in-app copy first, so the digest is waiting in the
            # bell even on a day the push itself never reaches the phone.
            push.notify(db, pref.user_id, payload["title"], payload["body"],
                        payload.get("url", "/"), kind="digest")
            pref.last_sent_on = now.date()
            db.commit()
            summary["sent"] += 1
    finally:
        db.close()
    return summary


def _loop() -> None:
    while True:
        try:
            run_once()
        except Exception as e:  # never let one bad pass kill the thread
            print(f"[digest] pass failed: {e}")
        time.sleep(CHECK_SECONDS)


def start() -> None:
    """Start the background thread once, if push is configured."""
    global _started
    if _started or not settings.push_enabled:
        if not settings.push_enabled:
            print("[digest] push not configured — daily notifications disabled")
        return
    _started = True
    threading.Thread(target=_loop, name="finmate-digest", daemon=True).start()
    print(f"[digest] daily notification scheduler running (checks every {CHECK_SECONDS}s)")
