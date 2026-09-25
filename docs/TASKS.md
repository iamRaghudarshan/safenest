# Tasks

**The owner's own reports come first.** Everything below that is derived by
diffing what the web app calls against what the phone calls — not written from
memory. The previous list was written from memory, scored itself, and reported
91% while 17 features were missing.

Nothing here is built or released without explicit approval, each time.

Last derived **25 September 2026** — server 3.45, phone 1.65.0.

---

## A — reported by the owner (do first)

### A1. People shows whole photographs, not faces — BLOCKED
Several circles show the same wide restaurant photo, so nobody can be told
apart, which makes naming them impossible.

*Cause:* the server sends `cover_url` and the phone crops to a `box` it does
not receive. The code that sends it is written, committed and pushed. **The
live server is still running old code** — `/api/gallery/labels` answers 404,
which is the marker for the old build.

*Blocked on:* `D:\AI PRO\finmate-react\Restart App API.bat`, run as
administrator. It cannot be done from a tool. **No further code will fix
this.**

### A2. Cannot name people person-by-person
Naming itself works. It is unusable because of A1: every circle looks like the
same photograph, so there is nothing to name. Clears when A1 clears.

### A3. Search a person by NAME
Typing a name already finds their photos — the text search matches a person's
name server-side. What is missing is anything that SAYS so: no suggestion, no
people row in the search screen. Until then it is a feature nobody can find.

### A4. Correcting a wrong grouping — nothing to correct it WITH
"Group again" is on the phone. The manual tools are not, and they are the ones
that matter when the automatic pass is wrong:
- merge two people into one
- split one person into two
- reassign a single mis-grouped face
- see every face filed under a person

All four exist on the web and on the server. This is the real answer to "faces
are grouped wrongly", and it should have shipped with the regroup button.

---

## B — documents, missing on the phone

| # | Task | Server | Web |
|---|---|---|---|
| B1 | Recycle bin: see it, restore, empty | ✅ | ✅ |
| B2 | Delete permanently | ✅ | ✅ |
| B3 | Replace a file (keeps the old as a version) | ✅ | ✅ |
| B4 | Correct the document type | ✅ | ✅ |
| B5 | Star / favourite a document | ✅ | ✅ |
| B6 | Copy a document | ✅ | ✅ |
| B7 | Recent — added, changed, starred | ✅ | ✅ |
| B8 | Values read out of a scan, offered to fill the form | ✅ | ✅ |

## C — photos, missing on the phone

| # | Task | Server | Web |
|---|---|---|---|
| C1 | Tag a person in a photo, and untag | ✅ | ✅ |
| C2 | Multi-select in the grid, and bulk favourite / trash | ✅ | ✅ |
| C3 | Bulk archive | ✅ | ✅ |
| C4 | Suggested albums, from clustering | ✅ | ✅ |

---

## Count

- Owner-reported: **4** (one blocked on a restart that only the owner can do)
- Derived missing on the phone: **12**
- **Phone parity: 54 of 71 = 76%**

The earlier claim of 91% counted a hand-written list. This one counts the web
app.

## Not in scope — decisions, not a backlog

Sharing with named people, public links, shared albums, comments,
collaboration, Docs-style editors. A Selfies category: nothing recorded
distinguishes one, since the stored EXIF has make and model but not which
camera took it.

## Cannot be checked here

Face precision and recall — needs real photographs. Scale beyond 10k items —
needs infrastructure. **Anything about how the phone LOOKS or BEHAVES:** no
Android SDK and no Xcode on this machine, so `flutter analyze` and the 239
tests prove it compiles and the logic holds, and prove nothing about the
screen. Every visual bug so far was found by the owner, not by CI.
