# Pending tasks

Against the 73-section brief. Status is evidence-based: **DONE** means
implemented *and* covered by a test that runs, with the test named. Nothing is
marked done on the strength of the code existing.

Last updated 23 September 2026.

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
| §26 | Wire `nlquery.parse` into `/api/gallery` | The parser works and nothing calls it | S |
| §26 | Plurals ("dogs" → `dog`) and multi-person ("Alice **and** Bob") | Both known-failing today | S |
| §24 | Re-measure `LABEL_MARGIN` on real photographs | Current 0.025 was tuned on *drawn* images | S |
| §33 | UI for correcting a document's type | Endpoint exists, nothing calls it | M |
| §50 | Surface reconciliation in Settings | Endpoint exists, nothing shows it | M |

### 2. Absent features
| # | Task | Size |
|---|---|---|
| §35 | File versions — keep, view, restore | M |
| §42 | Smart albums — saved rules (person + label + date) | M |
| §43 | Location: GPS → place names, map view, "photos in Goa" | L |
| §11 | Burst grouping | M |
| §10 | Near-duplicate review UI (dHash exists, no screen) | M |
| §37 | Recent / Starred / Shared-with-me surfaces | M |
| §39 §40 | Organisation suggestions, with approve/reject | L |
| §41 | Automatic creations — highlight reels, collages | L |
| §30 | Office-document preview (PDF and images only today) | M |

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
