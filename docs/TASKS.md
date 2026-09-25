# Tasks

**The owner's own reports come first.** Everything below that is derived by
diffing what the web app calls against what the phone calls — not written from
memory. An earlier list was written from memory, scored itself, and reported
91% while 17 features were missing.

Nothing here is built or released without explicit approval, each time.

Last derived **25 September 2026** — server 3.45, phone 1.65.0 (unreleased
work on `main`).

---

## A — reported by the owner

| # | Task | Phone | Note |
|---|---|---|---|
| A1 | People circles show whole photos, not faces | code done | **blocked on a server restart** |
| A2 | Name people one by one | code done | clears when A1 clears |
| A3 | Search a person by NAME | ✅ done | matching people now head the faces strip |
| A4 | Merge two people into one | ✅ done | Review faces → merge |
| A5 | Split one person into two | ✅ done | pick the wrong faces, "New person" |
| A6 | Reassign a mis-grouped face | ✅ done | "Move to…" or "Not a person" |
| A7 | See every face filed under a person | ✅ done | the Review faces grid |

### A1 — the one thing code cannot fix

Several circles showed the same wide restaurant photo, so nobody could be told
apart and naming them was impossible.

*Cause:* the server sends `cover_url` and the phone crops to a `box` it was not
being sent. The server code that sends it is written, committed and pushed.
**The live server is still running old code.**

*Checked against the real library on 25 September:* all 36 people have a cover
photo whose face row carries a usable box, so the crop will work the moment the
new code is running. The demo database could not show this — its photos had no
width or height recorded, so the box came back null and the first verification
run reported a failure that was in the fixture, not the product.

*Blocked on:* `D:\AI PRO\finmate-react\Restart App API.bat`, run as
administrator. It cannot be done from a tool. **No further code will fix this.**

*Note for that restart:* `people.is_me`, `people.is_hidden` and
`gallery_photos.place` are in the model and not yet in the live MySQL schema.
Migrations for all three exist in `main.py` and run at startup, so the restart
adds them — but a restart is required before any of those columns can be read.

---

## B — documents on the phone — all done

| # | Task | Phone |
|---|---|---|
| B1 | Recycle bin: see it, restore, empty | ✅ `doc_trash.dart` |
| B2 | Delete permanently | ✅ |
| B3 | Replace a file (keeps the old as a version) | ✅ |
| B4 | Correct the document type | ✅ |
| B5 | Star / favourite a document | ✅ |
| B6 | Copy a document | ✅ |
| B7 | Recent — added, changed, starred | ✅ `doc_recent.dart` |
| B8 | Values read out of a scan, offered to fill the form | ✅ |

## C — photos on the phone — all done

| # | Task | Phone |
|---|---|---|
| C1 | Tag a person in a photo, and untag | ✅ details sheet |
| C2 | Multi-select, bulk favourite / trash | ✅ now one request, not one per photo |
| C3 | Bulk archive | ✅ beside Delete in the selection bar |
| C4 | Suggested albums, from clustering | ✅ strip on the Albums tab |

Two bugs fell out of C2. The per-photo favourite route TOGGLES, so starring a
mixed selection un-starred the ones already starred — fifty photos of which ten
were starred lost those ten. And every bulk action now falls back to per-photo
calls on a 404, because a computer running an older SafeNest answers 404 and
without the fallback bulk actions would stop working the day the phone was
updated ahead of the computer, which is the normal order.

---

## Count

- Owner-reported: **7**, of which 5 are done and 2 are blocked on the restart.
- Derived missing on the phone: **0**.
- **Endpoint parity: 65 of 65.** The phone now calls every gallery, people and
  documents endpoint the web app calls.

## How that was checked

Endpoint parity alone proves the phone MENTIONS a path, which is the same blind
spot the 91% claim had. So each call was also sent to a running server with the
phone's own body shape — `scratchpad/phone_wire.py`, 36 checks, all passing.
That is what caught the routes where the phone sends `const {}` against a
router that declares a required body.

`flutter analyze` is clean and 246 tests pass.

## Not in scope — decisions, not a backlog

Sharing with named people, public links, shared albums, comments,
collaboration, Docs-style editors. A Selfies category: nothing recorded
distinguishes one, since the stored EXIF has make and model but not which
camera took it.

## Cannot be checked here

Face precision and recall — needs real photographs. Scale beyond 10k items —
needs infrastructure. **Anything about how the phone LOOKS or BEHAVES:** no
Android SDK and no Xcode on this machine, so `flutter analyze` and the tests
prove it compiles and the logic holds, and prove nothing about the screen.
Every visual bug so far was found by the owner, not by CI.
