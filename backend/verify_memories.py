"""Drive the Life Memory endpoints on a REAL running server.

Not a unit test. §13 of CLAUDE.md is explicit that verification here means a
throwaway script against a live server, and this module needs it more than most:
the phone holds the ORIGINAL and this database holds a copy, so the failure mode
is not "a request 500s" — it is "a retry quietly made a second memory", which no
unit test of the router in isolation can show.

Run it against a SECOND uvicorn with its own SQLite database — never the live one
on 8080, which holds real records:

    DB_ENGINE=sqlite DB_FILE=<scratch>/t.db JWT_SECRET=... MEDIA_SECRET=... \
    VAULT_KEY_HEX=<64 hex> venv/Scripts/python -m uvicorn app.main:app \
        --host 127.0.0.1 --port 8090

then:  venv/Scripts/python verify_memories.py <scratch-dir> [port]

The checks that matter, in order of how much damage they prevent:

  * PUSHING THE SAME MEMORY TWICE LEAVES EXACTLY ONE. A phone retries a push
    whose reply it never saw — that is the ordinary case here, not an edge — and
    a duplicate on this side cannot be told apart from a second thing somebody
    said.
  * said_at is kept as the phone sent it. Stamping arrival time instead puts a
    week of memories spoken abroad all on the afternoon of the flight home.
  * A batch with one bad item still lands the good ones. Failing whole would
    retry for ever and nothing behind the bad item would ever arrive.
  * One user cannot see or delete another's memories.
"""
import os, sys, json, urllib.request, urllib.error

S = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYNC_TEST_DIR", "")).replace("\\", "/")
PORT = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SYNC_TEST_PORT", "8090")
if not S:
    raise SystemExit("Give me the scratch folder the test server is using:\n"
                     "  python verify_memories.py <scratch-dir> [port]")

os.environ.update(DB_ENGINE="sqlite", DB_FILE=S + "/t.db", MEDIA_ROOT=S + "/media",
                  JWT_SECRET="test-only-secret-for-verification-not-real-0001",
                  MEDIA_SECRET="test-only-media-secret-for-verification-0002",
                  VAULT_KEY_HEX="1" * 64, PUBLIC_BASE_URL="")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uuid as _uuid

from app.database import SessionLocal
from app.models import Memory, MemoryFact, User, UserModule
from app.security import create_token, hash_password
from app import ist

db = SessionLocal()


def _user(email, name, role="admin"):
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, name=name, role=role,
                 password_hash=hash_password("x" * 14), created_at=ist.now())
        db.add(u)
        db.commit()
    if role != "admin":
        # A non-admin needs the grant, and granting it here is also the check
        # that the module key is the one the router guards on — a mismatch is
        # the "shipped but unreachable" trap, a module that 403s every call.
        have = (db.query(UserModule)
                .filter(UserModule.user_id == u.id,
                        UserModule.module_key == "memories").first())
        if not have:
            db.add(UserModule(user_id=u.id, module_key="memories",
                              can_view=1, can_create=1, can_edit=1, can_delete=1))
            db.commit()
    return u


me = _user("memories@test.local", "Memory Test")
other = _user("memories2@test.local", "Someone Else", role="user")
tok = create_token(me)
tok_other = create_token(other)

# A clean slate, so a second run cannot pass on the first run's rows.
db.query(MemoryFact).filter(MemoryFact.user_id.in_([me.id, other.id])).delete(synchronize_session=False)
db.query(Memory).filter(Memory.user_id.in_([me.id, other.id])).delete(synchronize_session=False)
db.commit()


def call(method, path, payload=None, token=None):
    body = None if payload is None else json.dumps(payload).encode()
    r = urllib.request.Request(
        "http://127.0.0.1:%s%s" % (PORT, path), data=body, method=method,
        headers={"Authorization": "Bearer " + (token or tok),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


def U():
    return str(_uuid.uuid4())


def memory(uuid, body, said_at, facts=(), spoken=True, row=1):
    return {"client_uuid": uuid, "device_row_id": row, "body": body,
            "said_at": said_at, "spoken": spoken, "facts": list(facts)}


print("1) a memory arrives from a phone")
WASHING = U()
st, r = call("POST", "/api/memories", memory(
    WASHING,
    "Bought the washing machine from Vijay Sales for Rs 32,400. Two year warranty.",
    "2026-09-20 18:30:00",
    facts=[
        {"kind": "expiry", "value": "Warranty ends 20 Sep 2028",
         "at": "2028-09-20 00:00:00"},
        {"kind": "amount", "value": "Rs 32,400"},
        {"kind": "shop", "value": "Vijay Sales"},
    ]))
check("accepted", st == 200, (st, r))
check("hands back an id the phone can store", isinstance(r.get("id"), int), r)
check("says it was new", r.get("duplicate") is False, r)
first_id = r.get("id")

st, listing = call("GET", "/api/memories")
check("it is in the list", st == 200 and listing["total"] == 1, listing)
row = listing["items"][0]
check("the words are verbatim",
      row["body"].startswith("Bought the washing machine from Vijay Sales"), row["body"])
check("it knows it was spoken", row["spoken"] is True, row)
check("all three confirmed facts came with it", len(row["facts"]) == 3, row["facts"])
check("the expiry kept its date",
      any(f["kind"] == "expiry" and "2028" in (f["at"] or "") for f in row["facts"]),
      row["facts"])

print("2) said_at is when it was SAID, not when it arrived")
# Otherwise a sync after a week away stamps seven days of memories with the same
# afternoon, and the thread on a new phone is nonsense.
check("the phone's time was kept", "2026-09-20" in str(row["said_at"]), row["said_at"])

print("3) pushing the same memory twice leaves exactly one")
# THE CHECK THIS FILE EXISTS FOR.
st, again = call("POST", "/api/memories", memory(
    WASHING, "Bought the washing machine from Vijay Sales for Rs 32,400.",
    "2026-09-20 18:30:00"))
check("the replay is accepted", st == 200, (st, again))
check("and says so", again.get("duplicate") is True, again)
check("it returns the ORIGINAL id", again.get("id") == first_id, (again, first_id))
st, listing = call("GET", "/api/memories")
check("still exactly one memory", listing["total"] == 1, listing["total"])

print("4) a replay refreshes the facts but never the words")
st, _ = call("POST", "/api/memories", memory(
    WASHING, "something completely different", "2026-09-20 18:30:00",
    facts=[{"kind": "shop", "value": "Vijay Sales"}]))
st, listing = call("GET", "/api/memories")
row = listing["items"][0]
check("the words are untouched",
      row["body"].startswith("Bought the washing machine"), row["body"])
check("the facts followed the phone", len(row["facts"]) == 1, row["facts"])

print("5) a memory with no words, and one with no uuid, are refused")
st, _ = call("POST", "/api/memories", {"client_uuid": U(), "body": "   "})
check("empty words refused", st == 400, st)
st, _ = call("POST", "/api/memories", {"body": "no uuid at all"})
# Refused rather than generated here: a server-minted key is useless, because the
# phone could not use it to recognise its own retry.
check("a missing uuid is refused", st == 400, st)

print("6) a batch, which is how a phone catches up")
ids = [U() for _ in range(3)]
st, batch = call("POST", "/api/memories/batch", {"items": [
    memory(ids[0], "Amma says tamarind first", "2026-09-22 09:00:00", row=11),
    memory(ids[1], "Spare key is with Ravi", "2026-09-23 09:00:00", row=12),
    memory(ids[2], "Mixer from Croma", "2026-09-24 09:00:00", row=13,
           facts=[{"kind": "shop", "value": "Croma"}]),
]})
check("accepted", st == 200, (st, batch))
results = batch.get("results", [])
check("one result per item", len(results) == 3, results)
# `all()` over an empty list is True, so these would have passed on a 500 that
# returned nothing — and on the first run they did. The length is part of the
# check.
check("each carries the phone's row id",
      len(results) == 3 and all(isinstance(x.get("device_row_id"), int) for x in results), results)
check("each carries a server id",
      len(results) == 3 and all(isinstance(x.get("id"), int) for x in results), results)
st, listing = call("GET", "/api/memories")
check("all four are there now", listing["total"] == 4, listing["total"])
check("newest first",
      "Mixer" in listing["items"][0]["body"], listing["items"][0]["body"])

print("7) one bad item in a batch does not take the good ones with it")
# Failing whole would retry for ever and nothing behind the bad item would ever
# arrive.
good = U()
st, batch = call("POST", "/api/memories/batch", {"items": [
    {"client_uuid": "", "device_row_id": 21, "body": "no uuid"},
    memory(good, "This one is fine", "2026-09-25 09:00:00", row=22),
    "not an object at all",
]})
check("the batch still returns 200", st == 200, st)
results = batch["results"]
check("the bad one reports its own error",
      any(x.get("error") for x in results), results)
check("the good one got through",
      any(x.get("id") for x in results if x.get("device_row_id") == 22), results)
st, listing = call("GET", "/api/memories")
check("exactly one more memory", listing["total"] == 5, listing["total"])

print("8) a replayed batch is still idempotent")
st, batch = call("POST", "/api/memories/batch", {"items": [
    memory(ids[0], "Amma says tamarind first", "2026-09-22 09:00:00", row=11),
    memory(good, "This one is fine", "2026-09-25 09:00:00", row=22),
]})
check("every item reports duplicate",
      len(batch.get("results", [])) == 2
      and all(x.get("duplicate") is True for x in batch["results"]),
      batch.get("results"))
st, listing = call("GET", "/api/memories")
check("nothing was added", listing["total"] == 5, listing["total"])

print("9) finding one again")
st, hits = call("GET", "/api/memories?q=tamarind")
check("the words are searched", hits["total"] == 1, hits["total"])
st, hits = call("GET", "/api/memories?q=Croma")
check("so are the confirmed facts", hits["total"] >= 1, hits["total"])
st, hits = call("GET", "/api/memories?q=nothinglikethis")
check("a miss is empty, not everything", hits["total"] == 0, hits["total"])

print("10) what is coming, for a phone that has no alarms set")
# ITS OWN MEMORY, and the first run of this file is why. The warranty pushed in
# step 1 belonged to a memory whose facts step 4 deliberately replaced, so by
# here there was no dated fact left anywhere and this section passed on an empty
# list — the test was wrong, not the endpoint. A check that needs a row has to
# make the row.
st, _ = call("POST", "/api/memories", memory(
    U(), "Fridge, five year warranty on the compressor.",
    "2026-09-27 10:00:00", row=41,
    facts=[{"kind": "expiry", "value": "Warranty ends 27 Sep 2031",
            "at": "2031-09-27 00:00:00"}]))
check("the dated memory landed", st == 200, st)
st, coming = call("GET", "/api/memories/coming")
check("responds 200", st == 200, st)
check("the warranty is listed",
      any("2031" in str(x["at"]) for x in coming["items"]), coming["items"])
check("and it carries the words it came from",
      bool(coming["items"]) and all(x.get("body") for x in coming["items"]),
      coming["items"])

print("11) somebody else's memories are not mine")
st, mine = call("GET", "/api/memories", token=tok_other)
check("another user sees none of them", mine["total"] == 0, mine["total"])
st, _ = call("DELETE", "/api/memories/%s" % first_id, token=tok_other)
check("and cannot delete one", st == 404, st)
st, _ = call("GET", "/api/memories")
check("it is still there", _["total"] == 6, _["total"])

print("12) an unauthenticated caller is refused")
try:
    urllib.request.urlopen("http://127.0.0.1:%s/api/memories" % PORT, timeout=20)
    check("401 without a token", False, "it answered!")
except urllib.error.HTTPError as e:
    check("401 without a token", e.code == 401, f"got {e.code}")

print("13) deleting the server's copy")
st, _ = call("DELETE", "/api/memories/%s" % first_id)
check("accepted", st == 200, st)
st, listing = call("GET", "/api/memories")
check("it is gone", listing["total"] == 5, listing["total"])
left = db.query(MemoryFact).filter(MemoryFact.memory_id == first_id).count()
check("its facts went with it", left == 0, left)
st, _ = call("DELETE", "/api/memories/%s" % first_id)
check("deleting it again is a 404, not a 500", st == 404, st)

print("14) a very long body is trimmed, not refused")
# A phone sending something odd must not get a memory stuck in its queue for
# ever: refusing is permanent, trimming is recoverable.
st, r = call("POST", "/api/memories",
             memory(U(), "x" * 20000, "2026-09-26 09:00:00", row=31))
check("accepted", st == 200, st)
st, listing = call("GET", "/api/memories?q=xxxxx")
check("stored within the column's limit",
      len(listing["items"][0]["body"]) <= 8000, len(listing["items"][0]["body"]))

print()
print(f"{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
