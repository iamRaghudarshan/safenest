"""Drive the sync endpoints on a REAL running server.

Not a unit test: §13 of CLAUDE.md is explicit that verification here means
throwaway scripts against a live server, because a middleware test that mocks
the middleware proves nothing. Kept rather than thrown away because offline
sync will be extended several times and this is the check that must keep
passing.

Run it against a SECOND uvicorn with its own SQLite database -- never the live
one on 8080, which holds real records:

    DB_ENGINE=sqlite DB_FILE=<scratch>/t.db JWT_SECRET=... MEDIA_SECRET=...     VAULT_KEY_HEX=<64 hex> venv/Scripts/python -m uvicorn app.main:app         --host 127.0.0.1 --port 8090

then:  venv/Scripts/python verify_sync.py

17 checks. The one that matters most is that VAULT IS NOT SYNCABLE: a phone
holding a usable copy of the vault would move every saved password onto the
device most likely to be lost.
"""
import os, sys, json, urllib.request, urllib.error

# The scratch folder the test server was started against, and the port it is on.
# Passed in rather than hard-coded: this script must point at the THROWAWAY
# database the second uvicorn is using, and a path baked in here would sooner or
# later be pointed at the real one.
#     python verify_sync.py <scratch-dir> [port]
S = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYNC_TEST_DIR", "")).replace("\\", "/")
PORT = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SYNC_TEST_PORT", "8090")
if not S:
    raise SystemExit("Give me the scratch folder the test server is using:\n"
                     "  python verify_sync.py <scratch-dir> [port]")

os.environ.update(DB_ENGINE="sqlite", DB_FILE=S + "/t.db", MEDIA_ROOT=S + "/media",
                  JWT_SECRET="test-only-secret-for-verification-not-real-0001",
                  MEDIA_SECRET="test-only-media-secret-for-verification-0002",
                  VAULT_KEY_HEX="1" * 64, PUBLIC_BASE_URL="")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import User, SyncOp
from app.security import create_token, hash_password
from app import ist
from app.routers.sync import prior, remember

db = SessionLocal()
u = db.query(User).filter(User.email == "sync@test.local").first()
if not u:
    u = User(email="sync@test.local", name="Sync Test", role="admin",
             password_hash=hash_password("x" * 14), created_at=ist.now())
    db.add(u); db.commit()
tok = create_token(u)

def get(path):
    r = urllib.request.Request("http://127.0.0.1:%s" % PORT + path,
                               headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, json.load(resp)

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1;  print(f"  PASS  {name}")
    else:    fail += 1; print(f"  FAIL  {name} {extra}")

print("1) capabilities")
st, cap = get("/api/sync/capabilities")
check("responds 200", st == 200)
check("declares a protocol number", isinstance(cap.get("protocol"), int))
check("lists modules", "expenses" in cap["modules"] and "todos" in cap["modules"])
check("VAULT IS NOT SYNCABLE", "vault" not in cap["modules"], cap["modules"])
check("gallery is not syncable", "gallery" not in cap["modules"])
check("documents are not syncable", "documents" not in cap["modules"])
check("declares the three ops", set(cap["ops"]) == {"create", "update", "delete"})
check("hands back its own clock", bool(cap.get("server_time")))
check("names the timezone", cap.get("timezone") == "Asia/Kolkata")

print("2) unauthenticated callers are refused")
try:
    urllib.request.urlopen("http://127.0.0.1:%s/api/sync/capabilities" % PORT, timeout=20)
    check("401 without a token", False, "it answered!")
except urllib.error.HTTPError as e:
    check("401 without a token", e.code == 401, f"got {e.code}")

print("3) the memory that stops duplicates")
db.query(SyncOp).filter(SyncOp.user_id == u.id).delete(); db.commit()
UUID = "11111111-2222-3333-4444-555555555555"
check("an unseen uuid is unknown", prior(db, u.id, UUID) is None)
remember(db, u.id, UUID, "expenses", "create", 4242); db.commit()
p = prior(db, u.id, UUID)
check("a replayed uuid is recognised", p is not None)
check("and returns what it produced", p and p.server_id == 4242, p.server_id if p else None)
check("a different uuid is still unknown", prior(db, u.id, "other-uuid") is None)
check("empty uuid never matches", prior(db, u.id, "") is None)

print("4) the same uuid cannot be recorded twice")
from sqlalchemy.exc import IntegrityError
remember(db, u.id, UUID, "expenses", "create", 9999)
try:
    db.commit(); check("unique constraint holds", False, "a second row was allowed")
except IntegrityError:
    db.rollback(); check("unique constraint holds", True)

print("5) another user's uuid is not mine")
u2 = db.query(User).filter(User.email == "sync2@test.local").first()
if not u2:
    u2 = User(email="sync2@test.local", name="Other", role="user",
              password_hash=hash_password("y" * 14), created_at=ist.now())
    db.add(u2); db.commit()
check("same uuid, other user, unknown", prior(db, u2.id, UUID) is None)


def post(path, payload):
    r = urllib.request.Request("http://127.0.0.1:%s" % PORT + path,
                               data=json.dumps(payload).encode(),
                               headers={"Authorization": "Bearer " + tok,
                                        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def replay(ops):
    st, out = post("/api/sync/replay", {"ops": ops})
    return st, (out.get("results") or [])


import uuid as _uuid
def U():
    return str(_uuid.uuid4())

# NOTE: expenses has no "title" -- it is category/note/amount/txn_date. This
# script was first written from memory and got it wrong, which is precisely the
# trap safenest-mobile/CLAUDE.md 9 records for the phone's field tables. Read
# the router before writing a payload.
print("6) an offline create reaches the server")
u1 = U()
op = {"client_uuid": u1, "module": "expenses", "op": "create",
      "payload": {"note": "Chai", "amount": 40, "category": "Food",
                  "txn_date": "2026-08-20"}}
st, res = replay([op])
check("replay answers 200", st == 200, st)
check("the create is accepted", bool(res) and res[0]["status"] == "ok", res)
made = res[0].get("server_id") if res else None
check("and reports the row it made", isinstance(made, int), made)

print("7) THE POINT: replaying it does not make a second one")
st, res2 = replay([op])
check("the retry is recognised", bool(res2) and res2[0]["status"] == "already", res2)
check("and points at the SAME row", res2[0].get("server_id") == made,
      "%s vs %s" % (res2[0].get("server_id"), made))
st, listed = get("/api/expenses")
titles = [e.get("note") for e in (listed.get("items") or [])]
check("exactly one Chai exists", titles.count("Chai") == 1, titles)

print("8) an edit made offline lands")
st, res = replay([{"client_uuid": U(), "module": "expenses", "op": "update",
                   "server_id": made, "payload": {"note": "Chai and samosa"}}])
check("the edit is accepted", bool(res) and res[0]["status"] == "ok", res)
st, listed = get("/api/expenses")
now = [e for e in listed["items"] if e["id"] == made]
check("the record actually changed", bool(now) and now[0].get("note") == "Chai and samosa", now)

print("9) an edit based on a stale copy is reported, not applied blindly")
st, res = replay([{"client_uuid": U(), "module": "expenses", "op": "update",
                   "server_id": made, "base_updated_at": "2020-01-01T00:00:00",
                   "payload": {"note": "SHOULD NOT WIN"}}])
check("it comes back as a conflict", bool(res) and res[0]["status"] == "conflict", res)
check("carrying the server's own copy",
      (res[0].get("server_row") or {}).get("note") == "Chai and samosa",
      res[0].get("server_row"))
st, listed = get("/api/expenses")
now = [e for e in listed["items"] if e["id"] == made]
check("and the record was NOT overwritten",
      bool(now) and now[0].get("note") == "Chai and samosa", now)

print("10) a delete lands, and deleting twice is not an error")
st, res = replay([{"client_uuid": U(), "module": "expenses", "op": "delete",
                   "server_id": made}])
check("the delete is accepted", bool(res) and res[0]["status"] == "ok", res)
st, res = replay([{"client_uuid": U(), "module": "expenses", "op": "delete",
                   "server_id": made}])
check("deleting an already-deleted row is not a failure",
      bool(res) and res[0]["status"] in ("already", "ok"), res)

print("11) one bad op does not take the good ones down with it")
st, res = replay([
    {"client_uuid": U(), "module": "expenses", "op": "create",
     "payload": {"note": "Bus", "amount": 20, "category": "Travel",
                  "txn_date": "2026-08-21"}},
    {"client_uuid": U(), "module": "vault", "op": "create", "payload": {}},
])
check("both are reported separately", len(res) == 2, res)
check("the good one landed", res[0]["status"] == "ok", res[0])
check("VAULT IS REFUSED", res[1]["status"] == "rejected", res[1])

print("12) rubbish is refused rather than crashing the batch")
st, res = replay([{"client_uuid": U(), "module": "expenses", "op": "update",
                   "payload": {"note": "no id"}}])
check("an update with no target is rejected",
      bool(res) and res[0]["status"] == "rejected", res)
st, res = replay([{"client_uuid": U(), "module": "expenses", "op": "update",
                   "server_id": 999999, "payload": {"note": "x"}}])
check("editing a row that is not there says so",
      bool(res) and res[0]["status"] == "gone", res)

db.close()
print("")
print("%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
