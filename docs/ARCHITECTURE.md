# SafeNest media architecture — how a photo actually travels

A map of the path a photo takes from a phone's camera roll to a face-tagged,
searchable row on the household computer, written from the code rather than
from intent. Where a comment in the source contradicts what the code does, this
document follows the code and says so.

Companion to `GOOGLE_PHOTOS_BEHAVIOR_MATRIX.md`, which judges the same system
against what a user expects. This one only describes it.

---

## 1. The shape in one paragraph

A Flutter app enumerates the phone's library directly, asks the household
computer which content hashes it already holds, and uploads the remainder over
either the LAN or a Cloudflare tunnel — single-shot under 16 MB, 4 MB resumable
chunks above it. A FastAPI server hashes, normalises, thumbnails and files each
arrival under `private/<module>/<user_id>/<variant>/`, writes a `GalleryPhoto`
row, and a background daemon later re-opens the original to run CLIP, YuNet
face detection, SFace recognition and OCR against it. Nothing leaves the
machine. There is no cloud tier and no account beyond the local one.

---

## 2. Phone side — `D:\AI PRO\safenest-mobile`

### Discovery and routing
`lib/discover.dart` sweeps the phone's own /24 on port 8080 for
`GET /api/health`, requiring the body to contain `finmate-api` (`discover.dart:83`)
so a router's admin page cannot be mistaken for the user's SafeNest. mDNS was
rejected (reasons at `discover.dart:19-22`). Before photos go to a LAN address
the app calls `/api/auth/me` and requires an `id` back (`backup.dart:391-395`) —
a private IP is not by itself proof it is *your* machine.

Route preference is LAN first, tunnel second (`backup.dart:385-398`). This is
preference, not gating: there is no Wi-Fi-only or metered check anywhere.

### The backup engine — `lib/backup.dart` (1,135 lines)
Enumerates via `PhotoManager.getAssetPathList(onlyAll: true, type: RequestType.common)`
(`:641-644`) in pages of 200 (`:658`), trusting platform newest-first ordering.
`PermissionState.limited` is correctly treated as failure (`:588-595`).

Three dedup layers, in increasing cost:

1. **Ledger skip, no file opened.** `alreadyBackedUp(id, modified, signature)`
   against SQLite (`offline/store.dart:639-668`). `signature = (w*31 + h)*31 + duration`
   — deliberately not byte size, which would require opening the file.
2. **Streamed sha256 + `POST /api/gallery/have`** (`:322`, `:345`). Hashes are
   cached in their own `asset_hashes` table, kept separate from the ledger so a
   hashed-but-unsent file is never mistaken for backed up (`store.dart:225-228`).
3. **Server's own answer.** A 200 carrying `duplicate: true` counts as `already`,
   not `stored` (`:992`).

Upload is single-shot below 16 MB (`_resumeFrom`, `:1072`) and chunked above.

### The offline queue — `lib/offline/`
SQLite (`offline.db`, version 6) with real migrations. `pending` and
`pending_files` are journals of *records*, encrypted with a hand-rolled
SHA-256 counter-mode stream + HMAC (the header says plainly it is not AES-GCM,
`store.dart:29-36`). Keyed FIFO by `seq`, `client_uuid UNIQUE`, deleted only
after server confirmation.

**Media never enters this queue**, and that is policy, not oversight —
`offline/mode.dart:80-82`: *"Your library is far too large to hold twice."*
A photo taken while the computer is unreachable is not queued; it is
re-discovered on the next manual run.

### What the engine does not do
No background execution of any kind — no `workmanager`, no `BGTaskScheduler`,
no foreground service; `Info.plist` declares only `remote-notification`. No
automatic trigger. Failure lists live in RAM and die with the process. Whole
files are read into memory before upload (`:961`) even on the resumable path.
Two comments (`backup.dart:19`, `backup_screen.dart:10`) claim scheduling and
background operation that do not exist.

---

## 3. Server side — `D:\AI PRO\finmate-react\backend`

### Layout
`storage.py` puts everything under `<MEDIA_ROOT>/<module>/<user_id>/<variant>/`,
variants `original` and `thumb`, with `PRIVATE_ROOT = BACKEND_DIR/private`.
`MODULES = (GALLERY, DOCUMENTS, AVATARS)` (`storage.py:103`) — note that
`partial/`, where in-flight chunked uploads accumulate, is **not** a member, so
it is invisible to every usage calculation.

### Ingest — `routers/gallery.py` (2,014 lines)
`POST /api/gallery/upload` is declared `def`, not `async def`, on purpose
(`gallery.py:1154-1182`) so FastAPI runs it in the threadpool and CPU-heavy
image work stays off the event loop. The chunked trio does **not** follow that
rule: `upload_chunk` is `async def` (`:1253`) and does synchronous file I/O
(`:1278-1288`) plus a whole-file read into `store_photo` on completion
(`:1299-1302`).

Chunk offsets are tracked by `os.path.getsize()` of the `.part` file — there is
no database row for an in-flight upload. A mismatched offset returns a 409 that
names the true resume point (`:1271-1274`). Parts live under the media root
rather than `/tmp` deliberately, so a 300 MB video survives a reboot
(`:1214-1217`) — but nothing ever deletes one unless the client asks.

Dedup is a read-then-write: SELECT on `content_hash | source_hash` (`:1670`),
then an unguarded INSERT (`:1738`). It is correct sequentially — it even
self-heals a wrong `source_hash` (`:1681-1692`) and un-trashes a match
(`:1677-1680`) — and has no protection at all against two devices arriving at
once, because neither hash column is `unique`.

Video gets an OpenCV poster frame and a `_faststart` re-mux. ffmpeg was
deliberately rejected, so there is no transcoding: an iPhone HEVC `.mov` is
stored as-is and will not play in Chrome or Firefox.

### Indexing — `indexer.py`
A daemon thread started from `main.py:686`, yielding while uploads are in
flight (`UPLOAD_QUIET_SECONDS`, `indexer.py:59`). Re-opens each original and
populates `PhotoVector` (CLIP ViT-B/32, quantised), `PhotoFace` (YuNet detect),
`Person`/`PhotoPerson` (SFace 128-d), and OCR text (RapidOCR). ~186 MB of ONNX
on disk, all CPU-only, all local.

It decodes with `Image.open` and no `exif_transpose`, so a portrait photo is
presented to the face detector rotated 90°.

When a file cannot be opened it stamps `ocr_at` with empty text
(`indexer.py:126-135`) and writes a null `PhotoFace` marker (`:165-172`),
explicitly so the row does not come back round. That is reasonable as a
work-queue rule and has the side effect that a *missing original* becomes
permanently indistinguishable from a processed one.

### Record sync — `routers/sync.py`
Separate from media entirely. `POST /api/sync/replay` takes up to 200 ops per
call, each processed and committed independently so one refusal cannot discard
nine good records (`:203-208`). Idempotency is a client-minted uuid backed by
`UniqueConstraint("user_id", "client_uuid")` (`models.py:1044`) — the one real
idempotency guarantee in the codebase — with the memo row inserted *before* the
handler so it rides the handler's own commit (`:297-304`).

This is the most carefully built part of the system. It is also the pattern the
media path does not use.

### Timers — `scheduler.py`
One daemon thread, 60-second tick, three passes: daily digest, today's
reminders, and a database-only backup snapshot. It imports `NotificationPref`,
`PushSubscription`, `Reminder` and `User` — **no gallery model appears anywhere
in it.** Nothing on any timer touches media, trash or `partial/`.

---

## 4. What a second device sees

Nothing, until it asks. There is no change feed, cursor, `since=` parameter,
ETag, WebSocket or SSE anywhere in the backend or the frontend. Both clients
re-fetch a whole module on mount. Deletions have no tombstone — an offline
device discovers one only by replaying an edit and receiving `status: "gone"`.

`/api/sync/capabilities` is a capability handshake, not a change cursor, and
notably does not advertise whether chunked upload exists, so a phone must probe
for `/upload/chunk` and interpret a 404.

---

## 5. Scale target

The brief named 100M files. At a conservative 3 MB that is 300 TB, which is not
a household and not this product. **This document sets the target at 1,000,000
items on one machine's disk** — a large multi-generation family library with
headroom — and the work is scoped to that number.

It matters concretely. At 1M rows the currently-unindexed `user_id`,
`is_trashed` and `taken_at` columns (`models.py:334-346`) turn every gallery
query into a full table scan, and an unvirtualised grid stops being usable long
before that. Both are on the fix list because of this number; a different
number changes both answers.

---

## 6. Design decisions worth not reversing

Several things that look like gaps are deliberate, and the reasoning is sound:

- **Media is excluded from the offline queue.** Holding the library twice on the
  phone is not viable (`offline/mode.dart:80-82`).
- **`.part` files live under the media root, not `/tmp`** — so they survive a
  reboot (`gallery.py:1214-1217`). The bug is the missing sweeper, not the location.
- **ffmpeg was rejected**, accepting no transcoding rather than a ~100 MB
  dependency. AI BIT made the same call for the opposite reason and kept it.
- **The upload endpoint is `def`, not `async def`**, to keep image work off the
  event loop (`gallery.py:1154-1182`). The chunked path should be brought into
  line with this, not the reverse.
- **Sync replays through the same handlers the web app uses** (`sync.py:177-195`)
  rather than reimplementing writes, with RBAC re-applied per op.
- **Device tokens are capability credentials, not device identities** — hashed,
  shown once, revocable, permission re-checked per upload (`devices.py:168-175`).
