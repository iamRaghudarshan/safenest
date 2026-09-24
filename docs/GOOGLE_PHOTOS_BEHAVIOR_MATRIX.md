# Google Photos behavioural parity — where SafeNest actually stands

Status: **Tier 1 is implemented** (23 September 2026); Tiers 2 and 3 are not.
Rows below are marked **FIXED** where the behaviour has actually changed, with
the verification script that proves it. Everything else is unchanged and still
live. Every claim carries a `file:line` so it can be re-checked rather than
believed.

Read the two framing notes at the bottom first if you are deciding scope — they
change what "parity" can mean here.

---

## How to read the verdicts

| mark | meaning |
|---|---|
| **Yes** | Behaviour exists and is wired to a real code path |
| **Partial** | Exists but with a limit that a user would notice |
| **No** | Absent. Not stubbed, not disabled — not present |
| ⚠ | Can lose data, or silently stop protecting the user |

---

## 1. Capture → backup (the phone)

| Google Photos behaviour | SafeNest | Evidence |
|---|---|---|
| Backs up without being asked | **FIXED, opt-in** | `lib/background.dart` schedules a periodic task through WorkManager (Android) and BGTaskScheduler + background fetch (iOS). Ships **off**: the product's own argument is that it does not copy a camera roll uninvited, so the behaviour is available rather than imposed. Wi-Fi-only defaults on |
| Runs in the background | **FIXED on Android; best-effort on iOS** | Android WorkManager is a real scheduler — it survives reboots (hence `RECEIVE_BOOT_COMPLETED`) and enforces the network and charging constraints itself. iOS BGTaskScheduler promises **nothing** about when it runs, or that it runs at all if the app is never opened; no API or entitlement changes that, so the settings row says "iOS decides when this runs" and shows what actually happened last time rather than implying a schedule |
| Survives leaving the screen | ⚠ **No** | `photos_home.dart:66-71` `dispose()` calls `_backup?.stop()`, and the screen is a **pushed route** (`home_screen.dart:226-228`) — pressing Back kills the run |
| Backs up videos | **FIXED** | `READ_MEDIA_VIDEO` now declared in `AndroidManifest.xml`. Previously the scan asked `RequestType.common` (photos + videos, `backup.dart:641-644`) against a permission that was never declared, so on Android 13+ videos were never enumerated — never counted, never failed, never reported. **Needs a new build to reach any phone.** |
| Wi-Fi-only / charging / metered gates | **FIXED** | A named choice — "Wi-Fi only" vs "Wi-Fi or mobile data" — plus charging-only and battery-not-low. Enforced by the OS scheduler as a constraint, not by us checking after an upload has already started. Two named options rather than one switch, because "Only on Wi-Fi — off" makes you infer the other state and the wrong inference costs somebody's data allowance |
| Retries a failed upload later | ⚠ **Partial, RAM-only** | `_failedAssets` / `_failReason` (`backup.dart:922,927`) cleared each run (`:568-570`), never persisted. Retry policy in full: LAN once → tunnel → fixed 1s → tunnel, **and only for status 0** (`backup.dart:409-420`). A timeout, 5xx or 429 is never retried |
| Resumable upload of large files | **Yes** | `_uploadResumable()` (`backup.dart:1079-1134`), 4 MB chunks, 409 re-sync, server `/upload/status` + `/upload/chunk` + `/upload/abandon` |
| Doesn't re-upload what is already there | **Yes, three layers** | Local ledger skip with no file opened (`store.dart:639-668`); streamed sha256 + `POST /api/gallery/have` (`backup.dart:322,345`); server replies `duplicate: true` counted as `already` not `stored` (`:992`) |

**The headline:** every dedup and resumption mechanism a mature client needs is
built and working. What is missing is the thing that makes a backup a backup —
**it only happens when a human presses a button, with the app open and the
screen awake.** Two comments in the source used to claim otherwise — that it
ran in the background, and that it could be scheduled. Both have been corrected
to describe what the code does, and to say plainly what it does not.

---

## 2. Storage and durability (the computer)

| Google Photos behaviour | SafeNest | Evidence |
|---|---|---|
| Trash empties itself after 30/60 days | **FIXED** | `GalleryPhoto.trashed_at` added and stamped at all four trash sites; `gallery.sweep_trash()` purges past `TRASH_RETENTION_DAYS = 30`, run once a day from `scheduler.run_sweeps()`. Rows binned before the column existed are **stamped, not purged**, so upgrading does not empty an existing bin. Proof: `backend/verify_photo_retention.py` |
| Photos survive a half-written upload | **Partial** | Chunked path exists; no temp-then-rename, no fsync, no post-write checksum verify |
| Sideways photos are shown upright | **FIXED** | Thumbnails are now built from an `exif_transpose`d copy, and the stored `width`/`height` are the displayed size so the justified timeline gives a portrait photo a portrait slot. Applied *after* hashing on purpose: `content_hash` comes from the normalised encoding, and transposing earlier would change the hash of every rotated photo already stored — the phone would stop recognising its own uploads and re-send the whole camera roll. OCR now transposes too |
| Videos play in any browser | **No** | No transcoding. OpenCV posters and a `_faststart` re-mux only; ffmpeg deliberately rejected. An iPhone HEVC `.mov` will not play in Chrome or Firefox |
| Multiple derivative sizes | **No** | One 480px thumb |
| Library stays fast as it grows | **FIXED (index)** | `ix_gallery_user_trash_taken` on `(user_id, is_trashed, taken_at)` — filters first, sort last, the order the planner can use in one pass. All three were unindexed, so every gallery query was a full table scan. The grid is virtualised now too; what remains unbuilt is the date scrubber |

**Correction to an earlier draft of this document.** It claimed the face pass
suffered the same rotation. It does not: face detection uses `cv2.imread`, and
OpenCV applies the orientation tag itself. Measured on this machine with a
400x200 image tagged `orientation=6` — PIL returns 400x200 (sideways), while
`cv2.imread` returns 200x400 (upright). So the gap was real for thumbnails, CLIP
(which reads the thumbnail) and OCR, and never existed for faces.

---

## 3. Timeline, search, people

| Google Photos behaviour | SafeNest | Evidence |
|---|---|---|
| Local face recognition | **Yes, real** | YuNet detect + SFace 128-d recognise, ~186 MB of ONNX on disk, verified present. `indexer.py` is a live daemon thread populating `PhotoFace`/`PhotoVector`/`Person`/`PhotoPerson` |
| Natural-language / semantic search | **Yes** | Quantised CLIP ViT-B/32 (85.0 + 61.5 MB), plus RapidOCR |
| Merge / split / hide a person | **No** | `people.py` offers rename and delete only |
| Photos ordered by when they were taken | **FIXED** | `shot_at` now sits between `taken_at` and `id` in every gallery ordering (main feed both directions, group covers, the on-this-day shelf). `taken_at` is a FlexDate — a *day* — so ordering on it alone left same-day photos tied and falling through to `id`, i.e. upload order. Photos with no EXIF time still fall through to `id`, which is the only honest answer for them |
| Date-grouped timeline with a scrubber | **No** | Flat grid |
| Grid stays smooth at 50k photos | **No** | Not virtualised |
| Text inside PDFs is searchable | **No** | OCR covers images only |
| Search is more than substring | **Partial** | `LIKE '%term%'` |

`shot_at` ordering was the cheapest high-impact fix on this page — a column
that already held the right data and was simply never sorted on. It is done.
Everything else in this section is still open, and people merge/split plus grid
virtualisation are the two that a large library will feel first.

---

## 4. Two devices, one library

| Google Photos behaviour | SafeNest | Evidence |
|---|---|---|
| Same photo from two phones stored once | **FIXED** | `UniqueConstraint("user_id", "content_hash", name="uq_gallery_user_content")` on `GalleryPhoto`, plus an `IntegrityError` branch in `store_photo` that drops the losing request's files and returns the winner as a duplicate. Migrations for both MySQL and SQLite; a database with pre-existing duplicates reports them and points at the Duplicates screen rather than deleting anything. Proof: `backend/verify_photo_dedup.py` |
| Record edits replay exactly once | **Yes** | `uq_sync_user_uuid` (`models.py:1044`) is the one real idempotency guarantee in the codebase, and the memo row is inserted *before* the handler so it rides the same commit (`sync.py:297-304`) |
| A second device learns of changes | **No** | No `/changes`, no cursor, no `since=`, no ETag, no WebSocket or SSE anywhere in backend or frontend. `/api/sync/capabilities` is a handshake, not a change feed. Both clients re-fetch on mount |
| Deletions propagate | **No tombstones** | An offline device finds out only when it replays an edit and gets `status: "gone"` (`sync.py:284-285`) |
| Interrupted uploads are reclaimed | **FIXED** | `gallery.sweep_partials()` reclaims `.part` files older than `PARTIAL_MAX_AGE_HOURS = 48`, on the same daily tick. `storage.PARTIAL` added to `MODULES` and an "Uploads in progress" slice added to the storage screen, so the space is visible while it exists. Proof: `backend/verify_photo_retention.py` |
| Disk and database agree | ⚠ **No, and drift is actively suppressed** | No reconciliation pass exists. `indexer.py:126-135` stamps `ocr_at` with empty text when a file cannot be opened *specifically so the row never comes back round*; `gallery.py:668-669` skips missing files with a bare `continue`. A missing original becomes permanently indistinguishable from a processed one. `diagnose` (`storage.py:220-353`), the one place meant to answer "what is wrong", never touches `PRIVATE_ROOT` |
| Permanent delete is atomic | ⚠ **No** | `gallery.py:2002-2011` removes files *then* commits the row delete. A failure in between leaves rows pointing at nothing — which, per the row above, nothing will ever notice |

Also fixed on the way: **`PhotoVector` was never deleted by anything** — not by
`destroy`, not by `empty_trash` — so every permanently deleted photo left its
CLIP embedding behind for ever. The cascade existed in two copies and both had
forgotten it; there is now one `purge_photos()` used by manual delete, empty-bin
and the automatic sweep.

One more, still open, outside the photo path but found on the way: **documents
have no deduplication at all** (`documents.py:253-280` writes a fresh `uuid4().hex` file
and row unconditionally), despite being excluded from `SYNCABLE` on the stated
grounds that they have "their own transfer machinery" (`sync.py:54-55`). They do
not. Re-uploading the same PDF twice always produces two copies.

---

## 5. Two framing notes that change the target

**SafeNest is not a cloud product, and its promise is the opposite of one.**
The storefront says *"It flows to your computer — never to us."* Several Google
Photos behaviours exist only because Google holds the files: cross-account
sharing, server-side ML at upload, unlimited tiers, web-scale availability.
Parity on those is not a missing feature, it is a different product. The
behaviours worth copying are the **observable ones a household notices**:
it happens by itself, nothing is lost, the timeline is in the right order,
search finds things, deleted means deleted eventually.

**The 100M-file target in the brief is not a household.** 100M photos at a
conservative 3 MB is 300 TB. The realistic ceiling for this product is a large
family library on one machine's disk. I would set the hard target at **1M items**
and write that number into the architecture rather than quietly ignoring the
brief. If you want a different number, say so and the indexing and pagination
work gets scoped to it — it materially changes whether SQLite-style full scans
are survivable and whether the grid needs virtualising.

**I will not claim any of this reproduces Google's internal algorithms.** What
is proposed is equivalent *observable behaviour* using SafeNest's own local
models.

---

## 6. What I would fix, in order

Ranked by harm prevented per unit of work, not by size.

**Tier 1 — DONE, 23 September 2026.** Verified by `verify_photo_dedup.py`,
`verify_photo_retention.py`, a clean `flutter analyze`, and a clean
`tsc --noEmit`. The Android permission needs a new build to reach a phone;
everything else is server-side and takes effect on restart.
1. [done] Declare `READ_MEDIA_VIDEO` in the Android manifest. One line; without it
   half the library is invisible on any modern Android and nothing says so.
2. [done] Unique index on `(user_id, content_hash)` + `IntegrityError` retry — closes
   the duplicate-row race the way `uq_sync_user_uuid` already closes the
   replay path. The pattern is in the codebase; it just was not applied here.
3. [done] Give `GalleryPhoto` a `trashed_at` and a scheduler purge, and sweep stale
   `.part` files on the same tick. Both currently fill the disk for ever.
4. [done] Sort the timeline by `shot_at`. One `ORDER BY`, data already correct.
5. [done] Add `partial` to `storage.MODULES` so the space at least shows up.
6. [done] Delete the two false comments claiming background execution exists.

**Tier 2 — next, and small**
7. `exif_transpose` at ingest — fixes sideways thumbnails **and** face recall
   together, since the indexer currently feeds YuNet a rotated image.
8. Index `user_id`, `is_trashed`, `taken_at`.
9. Commit the row delete before removing files, not after.
10. Persist the failed-upload list so a retry survives the app closing.

**Tier 3 — the real parity work (larger; worth agreeing scope first)**
11. ~~Automatic backup: background execution with Wi-Fi/charging gates.~~
    **Done** — see the rows above. What remains unbuilt is a *new-media
    observer*: the periodic task re-enumerates the library rather than being
    told a photo was taken, so a new photo waits for the next run instead of
    going immediately.
12. Stream uploads instead of `readAsBytes` (`backup.dart:961` loads whole files
    into RAM, 4 concurrent — the classic OOM on exactly the large libraries this
    product exists for). Same on the server: `upload_chunk` is `async def` but
    does blocking I/O and a whole-file read on the event loop.
13. A reconciliation pass that asks both halves of the question, and stops the
    indexer suppressing the evidence.
14. People merge/split/hide; grid virtualisation; date-grouped scrubber.

Tier 1 is roughly a day, each item independent and testable. Tier 2 is a second
day. Tier 3 is where the brief's real weight sits.


---

## Editing (added 24 September 2026)

| Google Photos behaviour | Status | Notes |
|---|---|---|
| Crop, rotate, flip | DONE | rendered from the pristine original every time, so a second crop is not a crop of a crop |
| Brightness / contrast / saturation / sharpness | DONE | clamped to 0.5x–2x; past that a slider stops being an adjustment |
| Filters | DONE | seven, each a plain function of an RGB image |
| Markup: pen, highlighter, arrow, shapes, text | DONE | coordinates are fractions, so the preview and the render cannot drift |
| Markup: redact | DONE, and stronger than Google's | the pixels are destroyed, not covered — a drawn box is undone by any other editor |
| Revert to original | DONE | the file the device sent is never overwritten |
| Video trim | DONE | lossless, no FFmpeg; starts on a keyframe and says where it landed |
| Video stabilise, speed, filters | NOT BUILT | each needs a re-encode, which needs a transcoder |
| Google Lens lookup | NOT BUILT | OCR and labels exist; pointing at an object to search it does not |

**The trade worth knowing about redaction.** Markup is part of the EDIT, so it
re-renders from the pristine original and "Use original" brings back what was
covered. That is right for a library — the alternative is a tool that destroys
the only copy of a photograph because somebody drew on it — but it means the
safe thing to send someone is the exported file, not the library copy.
