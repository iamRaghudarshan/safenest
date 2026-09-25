# Feature status

**One number, kept honest.** This file exists because the owner should never
have to ask "is it done?" — and because every time they did, the answer turned
out to be no. Reporting what was just built is not the same as auditing what
remains.

**A feature is DONE only when it exists on every surface that should have it.**
"Done on the web" is not done. That rule is what the audits kept catching.

Audited by grepping for real API call sites, not from memory. Comments and
docstrings match a grep and are not implementations.

Last audited **25 September 2026**, mobile 1.65.0 / server 3.45.

---

## Photos — 100% of what is in scope

| Feature | Server | Web | Phone |
|---|---|---|---|
| Timeline, paging, dedup on upload | ✅ | ✅ | ✅ |
| Faces & people, merge/split, correction | ✅ | ✅ | ✅ |
| **Face crop to the face, not the photo** | ✅ | ✅ | ✅ |
| **Regroup a badly grouped library** | ✅ | — | ✅ |
| Labels, natural-language search | ✅ | ✅ | ✅ |
| Places | ✅ | ✅ | ✅ |
| Albums, saved searches (view) | ✅ | ✅ | ✅ |
| **Saved searches (create)** | ✅ | ✅ | ✅ |
| Favourites, trash, 30-day retention | ✅ | ✅ | ✅ |
| **Archive (view and put things in)** | ✅ | ✅ | ✅ |
| Duplicates, near-duplicates | ✅ | ✅ | ✅ |
| Bursts | ✅ | ✅ | n/a |
| Memories / on this day | ✅ | ✅ | ✅ |
| Editing: crop, rotate, flip, adjust, filters | ✅ | ✅ | ✅ |
| Markup incl. redaction | ✅ | ✅ | ✅ |
| Video trim (lossless) | ✅ | ✅ | ✅ |
| Video speed / stabilise / colour | ✅ | ✅ | ✅ |
| Collages & moving highlights | ✅ | ✅ | ✅ |
| Suggestions with approve/reject | ✅ | ✅ | ✅ |
| Videos / Screenshots / Archive categories | ✅ | ✅ | ✅ |
| Share to the device's share sheet | — | ✅ | ✅ |
| Background backup from the phone | ✅ | n/a | ✅ |

## Documents — 100% of what is in scope

| Feature | Server | Web | Phone |
|---|---|---|---|
| Upload, scan, OCR, search inside | ✅ | ✅ | ✅ |
| Folders, breadcrumbs, move | ✅ | ✅ | ✅ |
| Multi-select and bulk actions | ✅ | ✅ | ✅ |
| **Filter by type and date** | ✅ | ✅ | ✅ |
| **Sort** | ✅ | ✅ | ✅ |
| Classification with correction | ✅ | ✅ | ✅ |
| **Versions: list, download, restore** | ✅ | ✅ | ✅ |
| Version retention (last 10) | ✅ | ✅ | ✅ |
| Preview: text, CSV, Office | ✅ | ✅ | ✅ |
| Preview: video, audio | ✅ | ✅ | ✅ |
| Trash and restore | ✅ | ✅ | ✅ |
| **Export as one zip** | ✅ | ✅ | ✅ |
| **Reconcile (disk vs database)** | ✅ | ✅ | ✅ |

---

## Deliberately not built

Not a backlog. These are decisions, and the reason is recorded so the question
does not get reopened by accident.

| Not built | Why |
|---|---|
| Sharing with named people, public links, shared albums | Needs accounts beyond the household and a public surface. The promise is that nothing leaves the owner's machine; sharing here is export-to-file. |
| Comments, suggestions, real-time collaboration | Requires multiple users reaching one server. |
| Docs/Sheets/Slides **editors** | Creating and editing documents is a different product. |
| Selfies category | Nothing recorded distinguishes one — the stored EXIF has make and model, not front/back camera. A tile that is empty or wrong is worse than an honest gap. |
| Organisation suggestions over *moves* | The clustering half exists. A wrong suggestion that FILES something is much more annoying than one that offers. |

## Cannot be proved on this machine

Counted here rather than quietly dropped.

| Unproven | Blocker |
|---|---|
| Face precision and recall | Needs real photographs of real people. Numbers will not be invented. |
| Scale at 1M–10M items, many concurrent users | Needs load infrastructure that does not exist here. The vector search is still a linear scan: fine at 10k, not at 1M. |
| **Anything about how the phone app LOOKS or BEHAVES** | No Android SDK, no Xcode. `flutter analyze` and 239 tests pass, which proves it compiles and the logic holds — it proves nothing about the screen. Three real bugs in 1.64.0 were found by the owner, not by CI. |

---

## The standing blocker

**The live server must be restarted after a release** or none of this reaches
anybody. `Restart App API.bat` needs an administrator click; it cannot be done
from a tool. Until it runs, a phone on the newest build talks to a server that
answers 404 to every new endpoint — which looks exactly like a broken app.
