"""Background photo indexing: face grouping and CLIP embeddings.

Both jobs are the same shape — walk the photos that haven't been processed yet,
run a model over each, write the result — and both are far too slow to do inside
a request. A single worker thread does them one photo at a time, so the app stays
responsive while a 5,000-photo library is worked through in the background.

Design notes:

* Resumable by construction. "What still needs doing" is a query, not a checkpoint,
  so a restart mid-run costs at most the photo in flight.
* One photo per transaction. A crash can't roll back an hour of work.
* Deliberately unhurried: a short sleep between photos keeps the machine usable —
  this is a laptop that also has to serve the app.
* Idempotent. Running it twice is a no-op for anything already indexed.
"""
import threading
import time

import numpy as np

from . import doctype, ist, ocr, storage, vision
from .database import SessionLocal
from .models import (Document, GalleryPhoto, Person, PhotoFace, PhotoLabel,
                     PhotoPerson, PhotoVector)

# Cosine threshold for "same person". SFace's own guidance is 0.363; 0.40 leaves a
# margin, because merging two people is far more annoying than splitting one.
FACE_MATCH = 0.40
CLIP_MODEL = "clip-vit-b32-q"

# Not every detected face belongs in People. A face on a scanned ID card, in a
# screenshot, or a 30-pixel head in the background of a crowd is not someone you
# photographed — and grouping those produces a People tab full of strangers and
# junk. Two cheap gates, both set from the real distribution in this library:
#
#   width  — SFace aligns to 112x112, so a small face is being upscaled and its
#            embedding is unreliable. 60px drops the bottom 2% (mostly spurious
#            detections) while keeping the 86px median of genuine portraits.
#   score  — YuNet detects down to 0.6; the bottom 1.3% sit below 0.8 and are
#            where the false positives live.
MIN_FACE_PX = 60
MIN_FACE_SCORE = 0.80

# Breathing room between photos so indexing never starves the web server.
REST_SECONDS = 0.02

# How long after the last upload before indexing picks up again.
#
# REST_SECONDS was not enough during a bulk upload. Indexing a photo means a CLIP
# embedding, OCR and face detection — seconds of CPU each — and every upload nudges
# a pass, so backing up a phone had the machine racing to index the first photos
# while the other 499 were still arriving. Measured on this machine, at the app's
# own concurrency of 4: 4.19 photos/sec uploaded with the indexer working against
# it, 6.57 with it out of the way. Over half the throughput of the one operation
# the person is sitting and watching, spent on work that has no deadline at all.
#
# So indexing yields while photos are arriving and resumes shortly after they stop.
# Nothing is skipped — the pass re-queries for pending work, so everything queued
# during the burst is picked up once it is over.
UPLOAD_QUIET_SECONDS = 3.0
_last_upload = 0.0

_state = {
    "running": False, "job": "", "done": 0, "total": 0,
    "started_at": None, "finished_at": None, "error": None,
    "faces_found": 0, "people": 0, "skipped_documents": 0, "text_found": 0,
}
_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def status() -> dict:
    """Progress, plus how much work is outstanding. Safe to poll."""
    db = SessionLocal()
    try:
        pending_faces = _pending_faces(db).count() if vision.faces_available() else 0
        pending_clip = _pending_clip(db).count() if vision.clip_available() else 0
        pending_ocr = ((_pending_ocr_photos(db).count() + _pending_ocr_docs(db).count())
                       if (ocr.available() or ocr.pdf_available()) else 0)
        total = db.query(GalleryPhoto).filter(GalleryPhoto.is_trashed == 0).count()
    finally:
        db.close()
    return {
        **_state,
        "models": vision.status(),
        "pending": {"faces": pending_faces, "clip": pending_clip, "ocr": pending_ocr},
        "photos": total,
    }


# ------------------------------------------------------------------ work lists
def _pending_faces(db):
    """Live photos with no face row yet. A photo genuinely containing no faces gets
    a marker row (person_id NULL, photo_id set) so it isn't rescanned forever."""
    scanned = db.query(PhotoFace.photo_id).distinct()
    return (db.query(GalleryPhoto)
            .filter(GalleryPhoto.is_trashed == 0, GalleryPhoto.id.notin_(scanned))
            .order_by(GalleryPhoto.id.desc()))


def _pending_clip(db):
    have = db.query(PhotoVector.photo_id)
    return (db.query(GalleryPhoto)
            .filter(GalleryPhoto.is_trashed == 0, GalleryPhoto.id.notin_(have))
            .order_by(GalleryPhoto.id.desc()))


def _pending_ocr_photos(db):
    return (db.query(GalleryPhoto)
            .filter(GalleryPhoto.is_trashed == 0, GalleryPhoto.ocr_at.is_(None))
            .order_by(GalleryPhoto.id.desc()))


def _pending_ocr_docs(db):
    return (db.query(Document)
            .filter(Document.is_trashed == 0, Document.ocr_at.is_(None))
            .order_by(Document.id.desc()))


# -------------------------------------------------------------------- text pass
def index_ocr_photo(db, photo: GalleryPhoto) -> int:
    """Read any text in one photo. Returns 1 if text was found."""
    path = storage.media_path(storage.GALLERY, photo.user_id, storage.ORIGINAL,
                              photo.filename)
    text = ""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            # Upright before reading. PIL hands back the sensor frame and
            # leaves the orientation tag for the caller to apply, so a receipt
            # photographed with the phone held upright arrived here on its side
            # and OCR returned nothing from it. (cv2.imread, used by the face
            # pass, applies the tag itself — PIL does not.)
            text = ocr.read_image(ImageOps.exif_transpose(im) or im)
    except Exception:
        text = ""
    # Stamped even when nothing was found — that is what stops this photo coming
    # back round on the next pass forever.
    photo.ocr_text = text
    photo.ocr_at = ist.now()
    db.commit()
    return 1 if text else 0


def index_ocr_doc(db, doc: Document) -> int:
    """Read the text of one document.

    Two routes, because documents arrive two ways. An image is OCR'd. A PDF
    has its embedded text layer read directly — no rendering and no OCR, which
    is what makes it affordable: most documents a household keeps are
    generated digitally and carry real text.

    This used to skip PDFs entirely, on the grounds that rendering a page
    needs an engine the app does not carry and "scans arrive as images
    anyway". The first half is still true and the second is not: an invoice
    emailed as a PDF is the commonest document there is, and searching for its
    number found nothing at all. A SCANNED pdf still returns nothing, because
    that genuinely does need rasterising — see ocr.read_pdf.
    """
    ext = (doc.ext or "").lower()
    text = ""
    if ext == "pdf":
        path = storage.media_path(storage.DOCUMENTS, doc.user_id, storage.ORIGINAL,
                                  doc.filename)
        text = ocr.read_pdf(path)
    elif ext in {"jpg", "jpeg", "png", "webp", "bmp", "heic", "heif"}:
        path = storage.media_path(storage.DOCUMENTS, doc.user_id, storage.ORIGINAL,
                                  doc.filename)
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as im:
                text = ocr.read_image(ImageOps.exif_transpose(im) or im)
        except Exception:
            text = ""
    # Suggest what kind of document this is, from the text just read.
    #
    # NEVER over a correction. kind_source == 'user' means somebody has already
    # said what this is, and a later indexing pass silently changing it back is
    # the behaviour that makes people stop trusting automatic filing.
    if (doc.kind_source or "auto") != "user":
        guess = doctype.classify(text)
        doc.kind = guess["kind"]
        doc.kind_confidence = guess["confidence"] or None
        doc.kind_source = "auto" if guess["kind"] else None

    doc.ocr_text = text
    doc.ocr_at = ist.now()
    db.commit()
    return 1 if text else 0


#: Known faces per user: [(person_id, embedding)], built once and kept.
#:
#: WHY THIS EXISTS
#: index_faces ran this query for EVERY PHOTO:
#:
#:     SELECT person_id, embedding FROM photo_faces
#:      WHERE user_id = ? AND person_id IS NOT NULL
#:
#: so indexing a library of N photos holding F assigned faces read and
#: unpacked F embeddings N times. At a few hundred photos that is invisible.
#: At fifty thousand photos and a hundred thousand faces it is five billion
#: vector unpacks, and the indexer appears to hang rather than to be slow.
#:
#: Held per user, appended to as faces are assigned, and thrown away whenever
#: the People tables change underneath it — see invalidate_people(). A stale
#: entry here would assign a face to a person who has just been merged away,
#: so the invalidation is not an optimisation detail, it is what makes the
#: cache safe.
_known: dict[int, list] = {}


def invalidate_people(user_id: int | None = None) -> None:
    """Forget the cached faces for a user, or for everybody.

    Called by every endpoint that moves a face between people or deletes a
    person. Cheap: the next photo to be indexed rebuilds it once.
    """
    if user_id is None:
        _known.clear()
    else:
        _known.pop(int(user_id), None)


def _known_faces(db, user_id: int) -> list:
    cached = _known.get(user_id)
    if cached is not None:
        return cached
    cached = [(f.person_id, vision.unpack(f.embedding, vision.FACE_DIM))
              for f in db.query(PhotoFace)
              .filter(PhotoFace.user_id == user_id,
                      PhotoFace.person_id.isnot(None),
                      PhotoFace.embedding.isnot(None)).all()]
    _known[user_id] = cached
    return cached


# ------------------------------------------------------------------- face pass
def index_faces(db, photo: GalleryPhoto) -> int:
    """Detect faces in one photo and attach each to a person. Returns the count."""
    import cv2
    path = storage.media_path(storage.GALLERY, photo.user_id, storage.ORIGINAL, photo.filename)
    bgr = cv2.imread(path)
    if bgr is None:
        # Unreadable file: record the attempt so it isn't retried every pass.
        db.add(PhotoFace(user_id=photo.user_id, photo_id=photo.id, person_id=None,
                         embedding=None, bbox=None, created_at=ist.now()))
        db.commit()
        return 0

    faces = vision.detect_faces(bgr)
    now = ist.now()

    # Is this a photo OF people, or a document that happens to contain a face?
    # Uses the CLIP vector from the earlier pass — which is why clip runs first.
    if faces:
        row = db.query(PhotoVector).filter(PhotoVector.photo_id == photo.id).first()
        if row and vision.looks_like_document(vision.unpack(row.vec)):
            db.add(PhotoFace(user_id=photo.user_id, photo_id=photo.id, person_id=None,
                             embedding=None, bbox="document", created_at=now))
            db.commit()
            _state["skipped_documents"] += 1
            return 0

    # Drop faces too small or too uncertain to identify reliably.
    faces = [f for f in faces
             if f["bbox"][2] >= MIN_FACE_PX
             and (f["score"] is None or f["score"] >= MIN_FACE_SCORE)]

    if not faces:
        db.add(PhotoFace(user_id=photo.user_id, photo_id=photo.id, person_id=None,
                         embedding=None, bbox=None, created_at=now))
        db.commit()
        return 0

    # Everyone already known for this user, so a new face joins them rather than
    # starting a duplicate person.
    known = _known_faces(db, photo.user_id)

    # Links added during THIS photo. A group shot can contain two faces that both
    # match the same person, and a "does the row exist?" query cannot see the row
    # added moments earlier in the same uncommitted transaction — which trips the
    # unique key on (photo_id, person_id).
    linked = {pid for (pid,) in db.query(PhotoPerson.person_id)
              .filter(PhotoPerson.photo_id == photo.id).all()}

    for face in faces:
        vec = face["embedding"]
        best_id, best_score = None, 0.0
        for pid, other in known:
            score = vision.cosine(vec, other)
            if score > best_score:
                best_score, best_id = score, pid

        if best_id and best_score >= FACE_MATCH:
            person_id = best_id
        else:
            count = db.query(Person).filter(Person.user_id == photo.user_id).count()
            person = Person(user_id=photo.user_id, name=f"Person {count + 1}",
                            cover_id=photo.id, created_at=now, updated_at=now)
            db.add(person); db.commit(); db.refresh(person)
            person_id = person.id
            _state["people"] += 1

        db.add(PhotoFace(user_id=photo.user_id, photo_id=photo.id, person_id=person_id,
                         embedding=vision.pack(vec),
                         bbox=",".join(str(v) for v in face["bbox"]),
                         score=face["score"], created_at=now))
        if person_id not in linked:
            db.add(PhotoPerson(photo_id=photo.id, person_id=person_id, created_at=now))
            linked.add(person_id)
        known.append((person_id, vec))

    db.commit()
    return len(faces)


# ------------------------------------------------------------------- CLIP pass
def index_clip(db, photo: GalleryPhoto) -> bool:
    """Embed one photo so it can be found by describing it."""
    from PIL import Image
    # The thumbnail is plenty: CLIP works at 224px, so decoding a 12 MP original
    # would cost far more time for an identical vector.
    path = storage.media_path(storage.GALLERY, photo.user_id, storage.THUMB, photo.filename)
    try:
        with Image.open(path) as im:
            vec = vision.embed_image(im)
    except Exception:
        vec = None
    if vec is None:
        return False
    db.merge(PhotoVector(photo_id=photo.id, user_id=photo.user_id, model=CLIP_MODEL,
                         vec=vision.pack(vec), created_at=ist.now()))

    # Labels come from the SAME vector, so this is one matrix multiply against
    # 57 pre-computed text embeddings — not a second model run. Replaced
    # wholesale rather than merged: the vocabulary can change between releases
    # and a stale label nobody can explain is worse than none.
    db.query(PhotoLabel).filter(PhotoLabel.photo_id == photo.id).delete(
        synchronize_session=False)
    now = ist.now()
    for name, score in vision.label_image(vec):
        db.add(PhotoLabel(user_id=photo.user_id, photo_id=photo.id,
                          label=name, score=float(score), created_at=now))
    db.commit()
    return True


# ---------------------------------------------------------------------- runner
# What each job needs: is it available, what is still outstanding, how to do one.
# A table rather than a chain of ifs, because the text pass reads two different
# kinds of row and the old "photo or photo" branch had nowhere to put that.
def _jobs_spec():
    return {
        "faces": [(vision.faces_available, _pending_faces,
                   lambda db, row: _state.__setitem__(
                       "faces_found", _state["faces_found"] + index_faces(db, row)))],
        "clip": [(vision.clip_available, _pending_clip,
                  lambda db, row: index_clip(db, row))],
        # Photos first: someone who just uploaded a receipt is looking at the
        # Gallery, not the Documents list.
        "ocr": [(ocr.available, _pending_ocr_photos,
                 lambda db, row: _state.__setitem__(
                     "text_found", _state["text_found"] + index_ocr_photo(db, row))),
                # Documents run when EITHER the OCR engine or the PDF reader
                # is available. Gating both on ocr.available() meant an install
                # without RapidOCR never read a PDF, although reading a text
                # layer needs no OCR at all.
                (lambda: ocr.available() or ocr.pdf_available(), _pending_ocr_docs,
                 lambda db, row: _state.__setitem__(
                     "text_found", _state["text_found"] + index_ocr_doc(db, row)))],
    }


def _run(jobs: tuple[str, ...]):
    db = SessionLocal()
    spec = _jobs_spec()
    try:
        for job in jobs:
            if _stop.is_set():
                break
            for is_ready, pending, do_one in spec.get(job, []):
                if _stop.is_set() or not is_ready():
                    continue
                total = pending(db).count()
                _state.update(job=job, done=0, total=total)
                if not total:
                    continue

                # Rows already attempted in THIS pass. A photo whose CLIP embedding
                # fails — a missing or unreadable thumbnail — is not written to
                # PhotoVector, so _pending_clip keeps handing it back. Without this
                # guard the batch re-query returned the same unfixable rows for ever:
                # the live indexer was found spinning on ten of them at
                # done=136017/total=10, wedged on "clip" so the faces pass that
                # follows it never ran and every "Find people" was refused with
                # "already running". Attempting each row at most once per pass
                # guarantees the loop drains even when some rows can never succeed;
                # a genuinely transient failure is retried on the next pass, which
                # starts with a fresh set.
                attempted: set[int] = set()
                while not _stop.is_set():
                    # Re-query each batch: the "not yet done" set shrinks as we go,
                    # and new uploads land in it while we work. Skip anything already
                    # tried this pass so a row that never clears cannot loop.
                    batch = [r for r in pending(db).limit(20).all()
                             if r.id not in attempted]
                    if not batch:
                        break
                    for row in batch:
                        if _stop.is_set():
                            break
                        # Per row, not per batch: twenty CLIP runs before yielding
                        # is most of a bulk upload's worth of contention.
                        _wait_for_quiet()
                        if _stop.is_set():
                            break
                        try:
                            do_one(db, row)
                        except Exception as exc:
                            db.rollback()
                            print(f"[indexer] {job} failed on row {row.id}: {exc}")
                        attempted.add(row.id)
                        _state["done"] += 1
                        time.sleep(REST_SECONDS)
    except Exception as exc:
        _state["error"] = str(exc)[:300]
        print(f"[indexer] aborted: {exc}")
    finally:
        db.close()
        _state.update(running=False, job="", finished_at=time.time())


def start(jobs: tuple[str, ...] = ("clip", "faces", "ocr")) -> dict:
    """Kick off a pass. Returns immediately; poll status() for progress."""
    global _thread
    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "already running", **_state}
        _stop.clear()
        _state.update(running=True, job=jobs[0] if jobs else "", done=0, total=0,
                      started_at=time.time(), finished_at=None, error=None,
                      faces_found=0, people=0, skipped_documents=0)
        _thread = threading.Thread(target=_run, args=(jobs,), daemon=True)
        _thread.start()
    return {"started": True, **_state}


def stop():
    _stop.set()


def note_upload() -> None:
    """Tell the indexer a photo has just arrived, so it stands aside.

    Called on every upload. Cheap by design — a timestamp, no lock: the worst a
    race can do is re-read a value that is about to be written again anyway.
    """
    global _last_upload
    _last_upload = time.time()


def uploading_now() -> bool:
    """True while photos are still landing.

    Read by anything else with no deadline — the folder watcher, for one — so a
    person watching an upload bar is never competing with background work.
    """
    return (time.time() - _last_upload) < UPLOAD_QUIET_SECONDS


def _wait_for_quiet() -> None:
    """Hold indexing while uploads are still coming in.

    Waits on the stop event rather than sleeping, so a stop during a long upload
    burst is acted on immediately instead of after the remaining wait.
    """
    while not _stop.is_set():
        quiet_for = time.time() - _last_upload
        if quiet_for >= UPLOAD_QUIET_SECONDS:
            return
        _stop.wait(min(UPLOAD_QUIET_SECONDS - quiet_for, 1.0))


def start_if_idle_work() -> None:
    """Called at boot. Only starts when there is genuinely something to do, so a
    fully-indexed library costs one query at startup and nothing more."""
    if not (vision.faces_available() or vision.clip_available()
            or ocr.available() or ocr.pdf_available()):
        return
    db = SessionLocal()
    try:
        todo = 0
        if vision.faces_available():
            todo += _pending_faces(db).count()
        if vision.clip_available():
            todo += _pending_clip(db).count()
        if ocr.available():
            todo += _pending_ocr_photos(db).count() + _pending_ocr_docs(db).count()
    finally:
        db.close()
    if todo:
        print(f"[indexer] {todo} photo pass(es) outstanding — indexing in the background")
        start()


# ---------------------------------------------------------------------- search
def search(db, user_id: int, query: str, limit: int = 300) -> list[tuple[int, float]]:
    """Photo ids ranked by how well they match a description, best first.

    Whole-library brute force: 5,000 × 512 floats is ~5 MB and one matrix multiply,
    which is quicker than any index would be at this size and needs no extra
    machinery to stay in sync.
    """
    wanted = vision.embed_text(query)
    if wanted is None:
        return []
    rows = (db.query(PhotoVector.photo_id, PhotoVector.vec)
            .filter(PhotoVector.user_id == user_id).all())
    # A truncated blob — a crash mid-write, or a row from an older model — would
    # make the reshape below fail and take the whole search down with it.
    expected = vision.CLIP_DIM * 2  # float16
    rows = [r for r in rows if r[1] and len(r[1]) == expected]
    if not rows:
        return []
    ids = np.fromiter((r[0] for r in rows), np.int64, len(rows))
    mat = np.frombuffer(b"".join(r[1] for r in rows), np.float16) \
            .astype(np.float32).reshape(len(rows), vision.CLIP_DIM)
    scores = mat @ wanted
    top = np.argsort(-scores)[:limit]
    return [(int(ids[i]), float(scores[i])) for i in top]
