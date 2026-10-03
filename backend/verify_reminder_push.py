"""Reminders must arrive ONCE. Driven against a REAL running server.

The owner reported "reminders are coming multiple times", and there were three
separate reasons, none of them visible from reading any one file:

  * The phone scheduled each reminder as a BURST of five local notifications
    thirty seconds apart. Android stacks rather than replaces, so one reminder
    left five entries in the shade. Fixed on the phone (alarms.dart) and pinned
    by test/alarms_test.dart.
  * This server pushed for the SAME reminder at the SAME minute, making six. A
    phone that rings its own alarm now says so when it registers, and the
    reminder push skips it.
  * And the FCM message carried no tag, so two server attempts stacked too —
    while every reminder shared one tag, which would have made two reminders due
    at the same hour collapse into one instead.

Run against a SECOND uvicorn with its own SQLite database — never the live one
on 8080:

    python verify_reminder_push.py <scratch-dir> [port]
"""
import os, sys, json, urllib.request, urllib.error

S = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYNC_TEST_DIR", "")).replace("\\", "/")
PORT = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SYNC_TEST_PORT", "8090")
if not S:
    raise SystemExit("python verify_reminder_push.py <scratch-dir> [port]")

os.environ.update(DB_ENGINE="sqlite", DB_FILE=S + "/t.db", MEDIA_ROOT=S + "/media",
                  JWT_SECRET="test-only-secret-for-verification-not-real-0001",
                  MEDIA_SECRET="test-only-media-secret-for-verification-0002",
                  VAULT_KEY_HEX="1" * 64, PUBLIC_BASE_URL="")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import timedelta

from app.database import SessionLocal
from app.models import Notification, PushSubscription, Reminder, User
from app.security import create_token, hash_password
from app import ist, push, scheduler

db = SessionLocal()
u = db.query(User).filter(User.email == "ring@test.local").first()
if not u:
    u = User(email="ring@test.local", name="Ring Test", role="admin",
             password_hash=hash_password("x" * 14), created_at=ist.now())
    db.add(u); db.commit()
tok = create_token(u)

# A clean slate so a second run cannot pass on the first run's rows.
db.query(Notification).filter(Notification.user_id == u.id).delete()
db.query(Reminder).filter(Reminder.user_id == u.id).delete()
db.query(PushSubscription).filter(PushSubscription.user_id == u.id).delete()
db.commit()

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1;  print(f"  PASS  {name}")
    else:    fail += 1; print(f"  FAIL  {name} {extra}")

def call(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    r = urllib.request.Request(
        "http://127.0.0.1:%s%s" % (PORT, path), data=body, method=method,
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:    return e.code, json.load(e)
        except Exception: return e.code, {}

TOKEN_LOCAL = "fake-fcm-token-for-a-phone-that-rings-its-own-alarms-0001"
TOKEN_PLAIN = "fake-fcm-token-for-a-phone-that-does-not-0002"

print("1) a device says whether it rings its own reminders")
st, r = call("POST", "/api/notifications/device",
             {"token": TOKEN_LOCAL, "platform": "android",
              "handles_reminders": True})
check("registered", st == 200 and r.get("registered"), (st, r))
row = db.query(PushSubscription).filter(
    PushSubscription.endpoint == TOKEN_LOCAL).first()
db.refresh(row) if row else None
check("the server remembered it", row is not None and row.handles_reminders == 1,
      row.handles_reminders if row else None)

st, r = call("POST", "/api/notifications/device",
             {"token": TOKEN_PLAIN, "platform": "android"})
plain = db.query(PushSubscription).filter(
    PushSubscription.endpoint == TOKEN_PLAIN).first()
check("a device that says nothing defaults to wanting the push",
      plain is not None and not plain.handles_reminders,
      plain.handles_reminders if plain else None)

print("2) it can change its mind, because the person can")
# A setting that only takes effect at the next sign-in is a setting that looks
# broken.
call("POST", "/api/notifications/device",
     {"token": TOKEN_LOCAL, "platform": "android", "handles_reminders": False})
db.expire_all()
row = db.query(PushSubscription).filter(
    PushSubscription.endpoint == TOKEN_LOCAL).first()
check("turning local alarms off brings the push back",
      row.handles_reminders == 0, row.handles_reminders)
call("POST", "/api/notifications/device",
     {"token": TOKEN_LOCAL, "platform": "android", "handles_reminders": True})
db.expire_all()

print("3) a reminder push skips the phone that is already ringing it")
res = push.notify(db, u.id, "Pay the electricity bill", "Due now — 6:30 pm",
                  "/reminders", kind="reminder", tag="reminder-1")
check("it was left to the phone's own alarm", res.get("rang_locally") == 1, res)
check("the other phone was still a target", res.get("phones") == 1, res)

print("4) ...but everything else still reaches it")
res = push.notify(db, u.id, "Your day", "Three things are due", "/",
                  kind="digest")
check("the daily summary goes to both phones", res.get("phones") == 2, res)
check("and nothing was skipped", res.get("rang_locally") == 0, res)

print("5) the bell is written either way")
# The stored row is what makes the bell truthful when a push is dropped by the
# OS, never permitted, or deliberately left to a local alarm.
rows = db.query(Notification).filter(Notification.user_id == u.id).all()
check("both notifications were recorded", len(rows) == 2, len(rows))
check("the reminder is among them",
      any(n.kind == "reminder" for n in rows), [n.kind for n in rows])

print("6) one reminder rings once a day, however often the pass runs")
when = ist.now() - timedelta(minutes=1)
r1 = Reminder(user_id=u.id, title="Bin day", due_date=ist.today(),
              due_time=when.strftime("%H:%M"), notify_push=1, notify_email=0,
              is_done=0, created_at=ist.now(), updated_at=ist.now())
db.add(r1); db.commit(); db.refresh(r1)
before = db.query(Notification).filter(Notification.user_id == u.id).count()

first = scheduler.run_reminders()
second = scheduler.run_reminders()
third = scheduler.run_reminders()
check("the first pass rings it", first.get("rang") == 1, first)
check("the second does not", second.get("rang") == 0, second)
check("nor the third", third.get("rang") == 0, third)
after = db.query(Notification).filter(Notification.user_id == u.id).count()
check("exactly one notification was written", after - before == 1,
      after - before)

print("7) each reminder gets a tag of its own")
# One tag for every reminder means two due at the same hour collapse into one on
# the phone and the first is never seen.
import inspect
src = inspect.getsource(scheduler.run_reminders)
check("the scheduler tags per reminder", 'tag=f"reminder-{r.id}"' in src, src[-400:])
fcm_src = inspect.getsource(__import__("app.fcm", fromlist=["send"]).send)
check("the FCM message carries the tag", '"tag":' in fcm_src)
check("and a collapse key, so a stale one is dropped",
      '"collapse_key"' in fcm_src)

print()
print(f"{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
