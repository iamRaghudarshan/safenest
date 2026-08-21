"""Daily digest scheduler, and reminders that name an hour.

A single daemon thread wakes every minute and does two things: sends the daily
summary to any user whose configured time has arrived and who hasn't been sent to
today, and rings any reminder set for this minute. A plain thread rather than
APScheduler/cron: this is one job on a single-instance app, and it must not add an
operational dependency to something the user runs by double-clicking.

Because the server is a PC that may be off, `last_sent_on` is a DATE rather than a
timestamp — if the machine was asleep at the send time and wakes later the same
day, the digest still goes out once, late, instead of being skipped entirely.
`Reminder.notified_on` is a DATE for the same reason and works the same way.
"""
import threading
import time
from datetime import date, datetime

from sqlalchemy import or_

from . import ist
from . import digest, mailer, push
from .config import settings
from .database import SessionLocal
from .models import NotificationPref, PushSubscription, Reminder, User

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
            # No longer skipped when the user has no push device. push.notify()
            # writes the in-app copy before it tries a device, so the daily summary
            # waits in the bell for a phone that never registered for push — which
            # is every iPhone here — instead of the summary simply never running
            # for them. The `no_devices` count stays for the audit line.
            if not db.query(PushSubscription).filter(
                    PushSubscription.user_id == pref.user_id).count():
                summary["no_devices"] += 1

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
            # The same summary as a branded email, where mail is set up and the
            # user has an address. Best-effort and queued; the bell copy above is
            # what everyone gets regardless.
            if (user.email or "").strip() and mailer.is_configured(db):
                mailer.enqueue(
                    db, user.email,
                    payload["title"],
                    payload["body"],  # plain-text fallback
                    kind="digest",
                    html=mailer.alert_html(
                        db,
                        title="Your daily summary",
                        intro=f"{payload.get('headline') or 'Here is what is coming up'}.",
                        items=payload.get("items")),
                )
            pref.last_sent_on = now.date()
            db.commit()
            summary["sent"] += 1
    finally:
        db.close()
    return summary


def run_reminders(now: datetime | None = None) -> dict:
    """Ring every reminder whose hour has come today.

    Separate from the digest on purpose. The digest is one message a day saying
    what is coming; this is the thing you asked to be told about at half past six,
    and being told about it at nine the next morning is not the same favour.

    Only TODAY's reminders. A reminder from three days ago whose hour passed while
    the machine was off does not ring now — it is already in the digest as overdue,
    and an alarm going off for something three days late reads as a fault. Within
    today the comparison is `now >= target`, not equality, so a PC that was asleep
    at 18:30 and woke at 21:00 still rings, once, late.
    """
    now = now or ist.now()
    today = now.date()
    summary = {"rang": 0}
    db = SessionLocal()
    try:
        # Either channel is reason enough to fire: a reminder that asked only for
        # an email must not be skipped because it did not also ask for a push.
        rows = db.query(Reminder).filter(
            Reminder.is_done == 0,
            or_(Reminder.notify_push == 1, Reminder.notify_email == 1),
            Reminder.due_time.isnot(None),
            Reminder.due_date == today,
        ).all()
        for r in rows:
            if r.notified_on == today:
                continue
            try:
                hh, mm = (int(x) for x in str(r.due_time).split(":")[:2])
            except (ValueError, IndexError):
                # Refused at the door by the router, so this is a row from before
                # that check or one edited in the database by hand. Marking it
                # handled stops the pass re-examining it every minute for ever.
                r.notified_on = today
                db.commit()
                continue
            if now < now.replace(hour=hh, minute=mm, second=0, microsecond=0):
                continue

            user = db.query(User).get(r.user_id)
            if not user or user.status != "active":
                continue

            when = digest.pretty_time(r.due_time)
            # The bell + push. notify() writes the in-app copy before attempting a
            # device, so the reminder is waiting in the bell even where push was
            # never permitted.
            if r.notify_push:
                push.notify(db, r.user_id, r.title or "Reminder",
                            f"Due now — {when}", "/reminders", kind="reminder")
            # And an email, if this reminder asked for one and mail is set up. The
            # `notify_email` flag was a dead control until now — stored and shown on
            # the form, acted on by nothing. Queued and best-effort, so a slow SMTP
            # never holds up the ring, and skipped silently where mail is not
            # configured (an end-user copy has no SMTP) rather than erroring.
            if r.notify_email and (user.email or "").strip() \
                    and mailer.is_configured(db):
                title = r.title or "Reminder"
                mailer.enqueue(
                    db, user.email,
                    f"Reminder: {title}",
                    f"{title} — due today at {when}.",  # plain-text fallback
                    kind="reminder",
                    html=mailer.alert_html(
                        db,
                        title=title,
                        intro="This reminder is due today.",
                        rows=[("When", f"Today at {when}")]),
                )
            r.notified_on = today
            db.commit()
            summary["rang"] += 1
    finally:
        db.close()
    return summary


def _loop() -> None:
    while True:
        # Two independent passes, each in its own try. A digest that throws must
        # not take the reminders down with it — they are the half someone is
        # sitting there waiting for.
        # Runs whether or not push is configured, like the reminders pass below:
        # the digest's in-app copy belongs in the bell even on an installation that
        # never set up VAPID. The push attempt inside notify() just no-ops there.
        try:
            run_once()
        except Exception as e:  # never let one bad pass kill the thread
            print(f"[digest] pass failed: {e}")
        try:
            run_reminders()
        except Exception as e:
            print(f"[reminders] pass failed: {e}")
        time.sleep(CHECK_SECONDS)


def start() -> None:
    """Start the background thread once.

    It now runs whether or not push is configured, which it did not before. A
    reminder set for half past six has to arrive at half past six on an
    installation with no VAPID keys too — push.notify() writes the in-app copy
    before it ever tries a device, so the bell is right even where the phone
    never rings. Gating the whole thread on push meant that on those
    installations the hour simply passed.
    """
    global _started
    if _started:
        return
    _started = True
    if not settings.push_enabled:
        print("[digest] push not configured — daily summaries off, "
              "timed reminders still appear in the app")
    threading.Thread(target=_loop, name="finmate-digest", daemon=True).start()
    print(f"[digest] scheduler running (checks every {CHECK_SECONDS}s)")
