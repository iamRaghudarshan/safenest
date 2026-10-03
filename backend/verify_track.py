"""Track Me, driven against a REAL running server.

Not a unit test. This module needs one more than most, for two reasons.

It is THE MOST SENSITIVE TABLE IN THE PRODUCT — a continuous record of where a
person is — so the question "can another account see it" is not a formality, and
neither is "does delete actually delete".

And it is IDEMPOTENT OR IT IS WRONG. The phone records all day with the screen
off and retries pushes whose replies it never saw, which is the ordinary case
here rather than an edge. A duplicated fix is not merely untidy: two fixes a
second apart at the same spot look like a stop, and a day full of them re-cuts
into places that were never places. A unit test of the router in isolation
cannot show that.

Run against a SECOND uvicorn with its own SQLite database — never the live one
on 8080:

    python verify_track.py <scratch-dir> [port]

The tile proxy is checked for its GUARDS only (zoom range, tile range, 401
without a token). Whether a real tile comes back depends on reaching
OpenStreetMap, and a verification script that fails when the internet is slow is
a script people learn to ignore.
"""
import os, sys, json, urllib.request, urllib.error

S = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYNC_TEST_DIR", "")).replace("\\", "/")
PORT = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SYNC_TEST_PORT", "8090")
if not S:
    raise SystemExit("python verify_track.py <scratch-dir> [port]")

os.environ.update(DB_ENGINE="sqlite", DB_FILE=S + "/t.db", MEDIA_ROOT=S + "/media",
                  JWT_SECRET="test-only-secret-for-verification-not-real-0001",
                  MEDIA_SECRET="test-only-media-secret-for-verification-0002",
                  VAULT_KEY_HEX="1" * 64, PUBLIC_BASE_URL="")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uuid as _uuid

from app.database import SessionLocal
from app.models import TrackPlace, TrackPoint, User, UserModule
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
        # Granting here is ALSO the check that the module key is the one the
        # router guards on. A mismatch is the "shipped but unreachable" trap,
        # and Life Memory nearly shipped with one — the phone called it `memory`
        # and the first cut of the server called it `memories`, so every
        # non-admin would have had the tile filtered off the Modules screen.
        have = (db.query(UserModule)
                .filter(UserModule.user_id == u.id,
                        UserModule.module_key == "track").first())
        if not have:
            db.add(UserModule(user_id=u.id, module_key="track",
                              can_view=1, can_create=1, can_edit=1, can_delete=1))
            db.commit()
    return u


me = _user("track@test.local", "Track Test")
plain = _user("track2@test.local", "Somebody Else", role="user")
other = _user("track3@test.local", "A Third", role="user")
tok = create_token(me)
tok_plain = create_token(plain)
tok_other = create_token(other)

for u in (me, plain, other):
    db.query(TrackPoint).filter(TrackPoint.user_id == u.id).delete()
    db.query(TrackPlace).filter(TrackPlace.user_id == u.id).delete()
db.commit()

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


def call(method, path, payload=None, token=None):
    body = None if payload is None else json.dumps(payload).encode()
    r = urllib.request.Request(
        "http://127.0.0.1:%s%s" % (PORT, path), data=body, method=method,
        headers={"Authorization": "Bearer " + (token or tok),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                return resp.status, json.load(resp)
            return resp.status, {"bytes": len(resp.read())}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def U():
    return str(_uuid.uuid4())


HOME = (12.9279, 77.6271)
OFFICE = (12.9716, 77.5946)


def fix(uuid, minute, place, row=1, accuracy=10.0):
    return {"client_uuid": uuid, "device_row_id": row,
            "at": f"2026-10-03 {6 + minute // 60:02d}:{minute % 60:02d}:00",
            "lat": place[0], "lon": place[1], "accuracy": accuracy,
            "battery": 80}


print("1) a batch of fixes arrives from a phone")
ids = [U() for _ in range(5)]
st, r = call("POST", "/api/track/batch", {"items": [
    fix(ids[i], i * 10, HOME, row=i + 1) for i in range(5)
]})
check("accepted", st == 200, (st, r))
check("five created", r.get("created") == 5, r)
check("one result per fix", len(r.get("results", [])) == 5, r.get("results"))
check("each carries the phone's row id",
      all(isinstance(x.get("device_row_id"), int) for x in r["results"]),
      r["results"])

st, listing = call("GET", "/api/track?day=2026-10-03")
check("they come back for that day", listing.get("count") == 5, listing)
check("oldest first",
      listing["items"][0]["at"] <= listing["items"][-1]["at"],
      [i["at"] for i in listing["items"]])
check("the time the fix was TAKEN is kept",
      "06:00" in str(listing["items"][0]["at"]), listing["items"][0]["at"])
check("accuracy travels, because the reader throws bad fixes away",
      listing["items"][0]["accuracy"] == 10, listing["items"][0])

print("2) pushing the same fixes again changes nothing")
# The ordinary case for this module, not an edge: a retry whose reply was never
# seen. Duplicates here look like a stop and re-cut the day into places that
# were never places.
st, r = call("POST", "/api/track/batch", {"items": [
    fix(ids[i], i * 10, HOME, row=i + 1) for i in range(5)
]})
check("accepted again", st == 200, st)
check("nothing was created", r.get("created") == 0, r)
check("every one says duplicate",
      all(x.get("duplicate") is True for x in r["results"]), r["results"])
st, listing = call("GET", "/api/track?day=2026-10-03")
check("still exactly five", listing.get("count") == 5, listing.get("count"))

print("3) a duplicate INSIDE one batch is caught too")
# Two phones, or one phone resending mid-batch. Checking only against the
# database would let a repeat within the same list through.
dup = U()
st, r = call("POST", "/api/track/batch", {"items": [
    fix(dup, 200, OFFICE, row=90), fix(dup, 200, OFFICE, row=91),
]})
check("only one was created", r.get("created") == 1, r)

print("4) one bad fix does not block the rest")
good = U()
st, r = call("POST", "/api/track/batch", {"items": [
    {"client_uuid": U(), "device_row_id": 60},               # no lat/lon
    {"client_uuid": "", "device_row_id": 61, "lat": 1, "lon": 1},
    {"client_uuid": U(), "device_row_id": 62, "lat": 999, "lon": 999},
    fix(good, 300, OFFICE, row=63),
    "not an object at all",
]})
check("the batch still returns 200", st == 200, st)
errs = [x for x in r["results"] if x.get("error")]
check("three were refused with reasons", len(errs) == 4, r["results"])
check("and the good one landed",
      any(x.get("id") for x in r["results"] if x.get("device_row_id") == 63),
      r["results"])
check("a point off the Earth is named as such",
      any("Earth" in str(x.get("error", "")) for x in r["results"]),
      r["results"])

print("5) which days have anything")
st, d = call("GET", "/api/track/days")
check("responds 200", st == 200, st)
check("the day is listed with a count",
      any(x["day"] == "2026-10-03" and x["fixes"] >= 5 for x in d["items"]),
      d["items"])

print("6) naming a place")
st, r = call("POST", "/api/track/places",
             {"name": "Home", "lat": HOME[0], "lon": HOME[1]})
check("named", st == 200 and r["item"]["name"] == "Home", (st, r))
place_id = r["item"]["id"]
check("it has a radius, because a house and its gate are one place",
      r["item"]["radius"] == 150, r["item"])

st, r = call("POST", "/api/track/places", {"name": "", "lat": 1, "lon": 1})
check("a place with no name is refused", st == 400, st)
st, r = call("POST", "/api/track/places", {"name": "Nowhere"})
check("a place with no coordinates is refused", st == 400, st)

st, r = call("GET", "/api/track/places")
check("it comes back in the list", len(r["items"]) == 1, r)

print("7) somebody else's history is not mine")
# Not a formality for this table.
st, r = call("GET", "/api/track?day=2026-10-03", token=tok_plain)
check("another account sees none of it", r.get("count") == 0, r)
st, r = call("GET", "/api/track/places", token=tok_plain)
check("nor the places", r["items"] == [], r)
st, r = call("DELETE", f"/api/track/places/{place_id}", token=tok_plain)
check("and cannot delete one", st == 404, st)

st, r = call("POST", "/api/track/batch",
             {"items": [fix(U(), 0, OFFICE, row=1)]}, token=tok_plain)
check("their own fixes are their own", r.get("created") == 1, r)
st, r = call("GET", "/api/track?day=2026-10-03", token=tok_other)
check("and invisible to a third account", r.get("count") == 0, r)

print("8) an unauthenticated caller is refused everywhere")
for path in ("/api/track", "/api/track/days", "/api/track/places",
             "/api/track/tile/12/2000/2000.png"):
    try:
        urllib.request.urlopen("http://127.0.0.1:%s%s" % (PORT, path), timeout=20)
        check(f"401 on {path}", False, "it answered!")
    except urllib.error.HTTPError as e:
        check(f"401 on {path}", e.code == 401, f"got {e.code}")

print("9) the tile proxy refuses what it should")
# The set of tiles somebody has fetched is itself a map of where they have been,
# so the cache is guarded like everything else — and the ranges stop a
# hand-written URL turning this into an open relay for somebody else's
# bandwidth.
st, r = call("GET", "/api/track/tile/1/0/0.png")
check("a zoom below the range is refused", st == 400, st)
st, r = call("GET", "/api/track/tile/22/0/0.png")
check("and above it", st == 400, st)
st, r = call("GET", "/api/track/tile/4/99999/0.png")
check("a tile off the edge of the world is refused", st == 400, st)

print("10) forgetting")
# No recycle bin here, and that is deliberate for this table alone: a location
# history is the one thing somebody may want gone in a hurry and gone
# completely, and a bin that quietly kept it would be the opposite of what the
# button says.
st, r = call("DELETE", "/api/track?day=2026-10-03")
check("a day can be forgotten", st == 200 and r.get("forgotten") >= 5, r)
st, listing = call("GET", "/api/track?day=2026-10-03")
check("and it is gone", listing.get("count") == 0, listing)
left = db.query(TrackPoint).filter(TrackPoint.user_id == me.id).count()
check("nothing was kept in a bin", left == 0, left)

st, r = call("DELETE", f"/api/track/places/{place_id}")
check("a place can be forgotten", st == 200, st)
st, r = call("GET", "/api/track/places")
check("and it is", r["items"] == [], r)

print("11) a batch that cannot work says so")
st, r = call("POST", "/api/track/batch", {"items": []})
check("an empty batch is refused", st == 400, st)
st, r = call("POST", "/api/track/batch",
             {"items": [fix(U(), 0, HOME) for _ in range(1500)]})
check("an enormous batch is refused rather than attempted", st == 400, st)

print()
print(f"{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
