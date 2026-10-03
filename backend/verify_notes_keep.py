"""Notes, driven against a REAL running server.

The owner's note: "Notes also not exactly like Google Keep — analyse and
implement it." Four things were missing on this side, and each is checked here.

  * A NOTE COULD NOT REMIND YOU. `reminder_at` has been on the model since the
    first release with a comment calling it stage 2, and nothing ever set it —
    stored, shown on the form, acted on by nobody.
  * NO "MAKE A COPY", which is the thing a note is most often wanted for: a
    shopping list you used last week, a packing list, a format.
  * NO BULK ACTION. Selecting eleven notes and archiving them was eleven round
    trips from a phone, each able to fail on its own, so a flaky connection left
    some archived and some not with no way to tell which.
  * AND PIN COULD NOT BE SET DIRECTLY, only toggled — which multi-select needs,
    because "pin all of these" is not "flip each of these".

Run against a SECOND uvicorn with its own SQLite database — never the live one
on 8080:

    python verify_notes_keep.py <scratch-dir> [port]
"""
import os, sys, json, urllib.request, urllib.error

S = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SYNC_TEST_DIR", "")).replace("\\", "/")
PORT = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SYNC_TEST_PORT", "8090")
if not S:
    raise SystemExit("python verify_notes_keep.py <scratch-dir> [port]")

os.environ.update(DB_ENGINE="sqlite", DB_FILE=S + "/t.db", MEDIA_ROOT=S + "/media",
                  JWT_SECRET="test-only-secret-for-verification-not-real-0001",
                  MEDIA_SECRET="test-only-media-secret-for-verification-0002",
                  VAULT_KEY_HEX="1" * 64, PUBLIC_BASE_URL="")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal
from app.models import Note, NoteItem, User
from app.security import create_token, hash_password
from app import ist

db = SessionLocal()
u = db.query(User).filter(User.email == "keep@test.local").first()
if not u:
    u = User(email="keep@test.local", name="Keep Test", role="admin",
             password_hash=hash_password("x" * 14), created_at=ist.now())
    db.add(u)
    db.commit()
tok = create_token(u)

db.query(NoteItem).filter(NoteItem.user_id == u.id).delete()
db.query(Note).filter(Note.user_id == u.id).delete()
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
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


print("1) a note can remind you")
st, r = call("POST", "/api/notes", {"title": "Call the plumber", "body": "9am"})
check("created", st == 200, (st, r))
nid = r["item"]["id"]
check("nothing is set yet", not r["item"].get("reminder_at"),
      r["item"].get("reminder_at"))

st, r = call("PUT", f"/api/notes/{nid}", {"reminder_at": "2026-10-10 09:00:00"})
check("a reminder can be put on it", st == 200 and r["item"].get("reminder_at"),
      r.get("item", {}).get("reminder_at"))
check("and it comes back on the list",
      any(n.get("reminder_at") for n in call("GET", "/api/notes")[1]["items"]))

st, r = call("PUT", f"/api/notes/{nid}", {"reminder_at": ""})
check("and it can be taken off again", not r["item"].get("reminder_at"),
      r["item"].get("reminder_at"))

print("2) pin can be SET, not only toggled")
# Multi-select needs this: "pin all of these" is not "flip each of these".
call("PUT", f"/api/notes/{nid}", {"pinned": True})
st, r = call("PUT", f"/api/notes/{nid}", {"pinned": True})
check("setting it twice leaves it pinned", r["item"]["pinned"] is True,
      r["item"])
st, r = call("PUT", f"/api/notes/{nid}", {"pinned": False})
check("and it can be set off", r["item"]["pinned"] is False, r["item"])

print("3) make a copy")
st, r = call("POST", "/api/notes", {
    "title": "Weekly shop", "kind": "checklist",
    "items": [{"text": "Rice", "checked": True},
              {"text": "Dal", "checked": True},
              {"text": "Oil", "checked": False}]})
check("a checklist to copy", st == 200, st)
list_id = r["item"]["id"]
call("PUT", f"/api/notes/{list_id}", {"pinned": True})

st, r = call("POST", f"/api/notes/{list_id}/copy")
check("copied", st == 200, (st, r))
made = r.get("item", {})
check("same title", made.get("title") == "Weekly shop", made.get("title"))
check("same lines", len(made.get("items", [])) == 3, made.get("items"))
# A copied shopping list with everything already ticked is of no use to anybody:
# the reason to copy one is to do it again.
check("nothing is ticked on the copy",
      all(not i["checked"] for i in made.get("items", [])), made.get("items"))
# Pinning says what is at the top of your list right now. A duplicate inheriting
# it pushes the original out of the way with something identical.
check("the copy is not pinned", made.get("pinned") is False, made.get("pinned"))
check("it is a new note", made.get("id") != list_id, made.get("id"))

st, r = call("POST", "/api/notes/999999/copy")
check("copying something that is not there is a 404, not a 500", st == 404, st)

print("4) one action over several notes")
ids = []
for i in range(4):
    st, r = call("POST", "/api/notes", {"title": f"Note {i}",
                                        "labels": ["Home"]})
    ids.append(r["item"]["id"])

st, r = call("POST", "/api/notes/bulk", {"action": "pin", "ids": ids})
check("four pinned in one call", r.get("changed") == 4, r)
check("and it says which, so Undo can name them",
      sorted(r.get("ids", [])) == sorted(ids), r.get("ids"))
items = {n["id"]: n for n in call("GET", "/api/notes")[1]["items"]}
check("they really are pinned", all(items[i]["pinned"] for i in ids))

st, r = call("POST", "/api/notes/bulk",
             {"action": "color", "ids": ids, "color": "teal"})
items = {n["id"]: n for n in call("GET", "/api/notes")[1]["items"]}
check("and recoloured", all(items[i]["color"] == "teal" for i in ids))

st, r = call("POST", "/api/notes/bulk",
             {"action": "label", "ids": ids, "labels": ["Trip"]})
items = {n["id"]: n for n in call("GET", "/api/notes")[1]["items"]}
# Labelling eleven notes "Trip" must not strip whatever else each was filed
# under.
check("labelling adds rather than replaces",
      all(set(items[i]["labels"]) == {"Home", "Trip"} for i in ids),
      [items[i]["labels"] for i in ids])

st, r = call("POST", "/api/notes/bulk",
             {"action": "unlabel", "ids": ids, "labels": ["Trip"]})
items = {n["id"]: n for n in call("GET", "/api/notes")[1]["items"]}
check("and it can be taken off again",
      all(items[i]["labels"] == ["Home"] for i in ids))

st, r = call("POST", "/api/notes/bulk", {"action": "archive", "ids": ids})
check("four archived", r.get("changed") == 4, r)
arch = {n["id"]: n for n in call("GET", "/api/notes?bucket=archived")[1]["items"]}
check("they are in the archive", all(i in arch for i in ids), list(arch))
# A pinned note at the top of the archive is a contradiction, and it would come
# back pinned.
check("archiving unpinned them", all(not arch[i]["pinned"] for i in ids))

st, r = call("POST", "/api/notes/bulk",
             {"action": "archive", "ids": ids, "value": False})
check("and Undo puts them back", r.get("changed") == 4, r)

print("5) a bulk call that cannot work says so")
st, r = call("POST", "/api/notes/bulk", {"action": "pin", "ids": []})
check("no ids is refused", st == 400, st)
st, r = call("POST", "/api/notes/bulk", {"action": "explode", "ids": ids})
check("an unknown action is refused", st == 400, st)
st, r = call("POST", "/api/notes/bulk", {"action": "pin", "ids": [999999]})
check("somebody else's ids change nothing", r.get("changed") == 0, r)

print()
print(f"{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
