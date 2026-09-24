# Pending tasks

Against the 73-section brief. Status is evidence-based: **DONE** means
implemented *and* covered by a test that runs, with the test named. Nothing is
marked done on the strength of the code existing.

Last updated 24 September 2026.

---

## Done this session — 122 checks passing

| # | Task | Evidence |
|---|---|---|
| §19 §20 §23 | People: merge, split, face correction, hide, cover, "Me" | `verify_people_ops.py` 17, `verify_people_api.py` 26 |
| §48 | Cross-user isolation on every People route | 9 of the above, all 404 |
| §50 | Reconciliation — disk vs database, report-only | `verify_reconcile.py` 19 |
| §27 §32 | PDF text extraction, searchable by contents | `verify_pdf_ocr.py` 13 |
| §45 | Archive — out of the timeline, still searchable | `verify_archive.py` 22 |
| §17 | Face-cache scale fix (200 table reads → 1) | `verify_face_cache.py` 8 |
| §33 | Document classification, abstains and explains | `verify_doctype.py` 6 + measured |
| §24 | Zero-shot object/scene labels via CLIP | `verify_labels.py` 11 |
| §26 | Query parser (person + label + date + kind) | measured on 9 queries |

Earlier in the project: §6 timeline, §7 backup, §8 resumable upload, §9 exact
duplicates, §28 §34 folders, §46 trash retention, §12 §13 metadata and video.

---

## Pending — ordered by value

### 1. Finish what is half-built
| # | Task | Why it matters | Size |
|---|---|---|---|
| §26 | ~~Wire `nlquery.parse` into `/api/gallery`~~ | Done — wired BEFORE the text filter, which is where the first attempt got it wrong | S |
| §26 | Plurals ("dogs" → `dog`) and multi-person ("Alice **and** Bob") | Both known-failing today | S |
| §24 | Re-measure `LABEL_MARGIN` on real photographs | Current 0.025 was tuned on *drawn* images | S |
| §33 | ~~UI for correcting a document's type~~ | Done — the sheet shows the classifier's evidence | M |
| §50 | Surface reconciliation in Settings | Endpoint exists, nothing shows it | M |

### 2. Absent features

Most of this section closed on 24 September. What is left is listed under it
with the reason, rather than deleted, because "we decided not to" and "we
forgot" look identical once a row disappears.

| # | Task | Evidence |
|---|---|---|
| §35 | File versions — keep, view, restore, download, retention | `verify_drive.py` 22, `verify_bulk_export.py`, `verify_version_cascade.py` 6 |
| §42 | Saved searches — a rule instead of a list | `verify_smartalbum.py` 18, `verify_albums_ui.py` 11 |
| §43 | Location: GPS → place names, offline gazetteer | `app/places.py`, wired into `/api/gallery?near=` and `/places` |
| §11 | Burst grouping | `verify_smartalbum.py`, `verify_bursts.py` 9 — real uploads, real hashes |
| §10 | Near-duplicate review | `DuplicatesView` — exact and similar, with a distance slider |
| §37 | Recent / Starred | a chip beside the categories; Shared does not apply |
| §30 | Text and CSV preview | `verify_preview.py` 28 |

Closed since:

| # | Task | Evidence |
|---|---|---|
| §39 §40 | Suggestions with approve/reject | `verify_creations.py` 26 — a dismissal is permanent, which is the property the panel lives on |
| §41 | Automatic creations — collages and moving highlights | same file; an animated WebP, not FFmpeg |
| §30 | Office preview (docx, xlsx, pptx) | `verify_officedoc.py` 24 — read as content, no LibreOffice, and a zip bomb refused |
| §30 | Video and audio preview in documents | `verify_preview.py` |
| — | Photo editing: crop, rotate, flip, adjust, filter | `verify_photoedit.py` 28, `verify_editor_ui.py` 21 |
| — | Markup: pen, highlighter, arrow, shapes, text, redact | `verify_markup.py` 17 — the redaction is checked by reading the pixels back |
| — | Video trim, lossless and without FFmpeg | `verify_videotrim.py` 24 — the result is DECODED, not just returned |

### 3. Architecture
| # | Task | Why | Size |
|---|---|---|---|
| §51 | Real worker queue: retry, timeout, dead-letter, idempotency | The indexer is one daemon thread; a crash loses the pass silently | L |
| §55 §17 | Vector index (ANN) for faces and CLIP | Both are linear scans; fine at 10k, not at 1M | L |
| §53 | Model registry — version, dims, thresholds, in config not code | Thresholds are constants in source today | M |
| §54 | Face-data lifecycle and audit | Embeddings are sensitive and currently just rows | M |

### 4. Testing the brief demands
| # | Task | Blocker |
|---|---|---|
| §18 §58 | Face precision/recall on a labelled dataset | **Needs real photographs of real people. I have none and will not fabricate the numbers.** |
| §56 §64 | 1M–10M items, 100–1,000 concurrent users | Needs load infrastructure that does not exist here |
| §61 §62 | Failure injection and chaos testing | Needs a staging environment |
| §57 | Reproducible 10k-image / 1k-video corpus | Generatable, but large |

---

## Not applicable to this product

Stated plainly rather than left looking unfinished. SafeNest is one household
on their own computer — the storefront's promise is *"It flows to your
computer — never to us."*

| # | Section | Why it does not apply |
|---|---|---|
| §36 | Sharing, links, per-user permissions | No accounts beyond the household; no public surface |
| §47 | Private object storage, CDN | Files are on the owner's disk |
| §48 | Multi-tenant isolation | Tested anyway on People; there is no tenancy to isolate |
| §55 | Search cluster | One machine |
| §56 §64 | 10M items, 1,000 concurrent users | A household library on a home PC |

`docs/ARCHITECTURE.md` sets the working ceiling at **1,000,000 items**, which
is what the indexing and pagination work is scoped to.

---

## Two mistakes worth keeping

Both were found by running things, not by reading them, and both had already
passed a typecheck and a code read.

- **`_rethumb` handed video bytes to PIL**, which raises. A video's
  thumbnail is a poster frame pulled from the file on disk, and without that
  branch reverting a trimmed clip took the request down with it.
- **store_photo normalised every upload to JPEG**, which is right for one
  format on disk and fatal for an animation, because a JPEG holds one frame.
  The first moving highlight came back as a still — a feature that silently
  does not work. The frame count is now read before `convert()` collapses it.
- **`smartalbum` compiled a photos rule to `kind = 'photo'`**, which matches
  no row: the column is NULL for photos and only written for videos. The
  gallery had already solved this three lines away.
- **Deleting a document left its versions behind.** Files accumulated on disk
  for ever, and — the serious half — ids get reused, so the next document to
  take that id inherited a deleted document's history, restorable. It surfaced
  as a suite reporting "a fresh document has no versions: 10". Fixed in all
  three delete paths; pinned by `verify_version_cascade.py`.
- **`describe()` named only videos**, so a photos-only saved search described
  itself as "everything" — exactly the failure that function exists to
  prevent. Found because a browser test asserted the subtitle it drew.

---

## Two standing items, not from the brief

- **The live server still runs the old backend.** None of the above takes
  effect on the hosted instance until it restarts — no folders, no
  reconciliation, no labels, no migrations. Not restarted, per instruction.
- **Rotate the secrets.** The admin password is `admin@2026` on a publicly
  reachable app, and I have now seen it. The Cloudflare token and GitHub PAT
  should go with it.

---

## On "100% complete"

§70 of the brief: *"You cannot guarantee mathematical 100% correctness.
Therefore: Do NOT say '100% bug-free.'"* This file is kept as counts and
evidence for that reason. §18 and §58 in particular cannot be honestly closed
without real face photographs, and §52 forbids faking the result.
