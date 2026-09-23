"""Does the database still agree with the disk?

Nothing in this application has ever asked. The two halves drift for ordinary
reasons — a delete that removed files and then failed before the row went, a
half-finished upload, a file lost to a drive that was unplugged mid-write, a
restore from a backup taken at a different moment — and every component that
could have noticed is written to look away:

  * indexer.py stamps ocr_at with empty text when a file cannot be opened, and
    writes a null PhotoFace marker, SPECIFICALLY so the row stops coming back.
    That is correct as a work-queue rule and it makes a missing original
    permanently indistinguishable from a processed one.
  * gallery.repair_videos skips unreadable rows with a bare `continue`.
  * storage.usage_everyone() measures the disk and never compares it to the
    database at all.
  * diagnose() in routers/storage.py — the one place meant to answer "what is
    wrong" — never touches PRIVATE_ROOT.

So a photo can vanish from disk while its row, its thumbnail entry, its face
embeddings and its search text all carry on as though it were fine. The
gallery shows a broken tile. Nobody is told.

WHAT THIS DELIBERATELY DOES NOT DO
It does not delete anything, ever. A missing file might be a drive that is
unplugged rather than data that is gone, and a stray file might be the only
surviving copy of something whose row was lost. Deleting on either signal
turns a recoverable situation into a permanent one. It reports, and a human
decides.
"""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from . import storage
from .models import Document, GalleryPhoto, PhotoFace, PhotoVector

#: Never return more than this many examples per category. The counts are the
#: answer; the samples are only so somebody can go and look at one. A library
#: that has lost ten thousand files should not produce a ten-thousand-line
#: response nobody can read.
SAMPLE = 25


def _disk_names(module: str, user_id: int, variant: str) -> set[str]:
    d = storage.media_dir(module, user_id, variant)
    if not os.path.isdir(d):
        return set()
    try:
        with os.scandir(d) as it:
            return {e.name for e in it if e.is_file()}
    except OSError:
        return set()


def _thumb_name(p: GalleryPhoto) -> str:
    """Mirror of gallery.thumb_name without importing the router.

    A video's poster is stored as `<filename>.jpg`, so comparing the raw
    filename against the thumb directory reports every video in the library as
    a missing thumbnail.
    """
    if (p.kind or "photo") == "video":
        return f"{p.filename}.jpg"
    return p.filename


def check_user(db: Session, user_id: int) -> dict:
    """Compare one user's rows against their files. Reads only."""
    out: dict = {"user_id": user_id}

    # ---- gallery ----------------------------------------------------------
    photos = (db.query(GalleryPhoto)
              .filter(GalleryPhoto.user_id == user_id).all())
    originals = _disk_names(storage.GALLERY, user_id, storage.ORIGINAL)
    thumbs = _disk_names(storage.GALLERY, user_id, storage.THUMB)

    missing_file, missing_thumb = [], []
    claimed, claimed_thumbs = set(), set()
    for p in photos:
        if not p.filename:
            continue
        claimed.add(p.filename)
        claimed_thumbs.add(_thumb_name(p))
        if p.filename not in originals:
            missing_file.append({"id": p.id, "filename": p.filename,
                                 "trashed": int(p.is_trashed or 0)})
        elif _thumb_name(p) not in thumbs:
            # `elif`: a photo whose ORIGINAL is gone has bigger problems than
            # its thumbnail, and listing it twice reads as two faults.
            missing_thumb.append({"id": p.id, "filename": p.filename})

    stray = sorted(originals - claimed)

    out["gallery"] = {
        "rows": len(photos),
        "files": len(originals),
        "missing_file": len(missing_file),
        "missing_thumb": len(missing_thumb),
        "stray_file": len(stray),
        "samples": {
            "missing_file": missing_file[:SAMPLE],
            "missing_thumb": missing_thumb[:SAMPLE],
            "stray_file": stray[:SAMPLE],
        },
    }

    # ---- documents --------------------------------------------------------
    docs = db.query(Document).filter(Document.user_id == user_id).all()
    doc_files = _disk_names(storage.DOCUMENTS, user_id, storage.ORIGINAL)
    doc_missing, doc_claimed = [], set()
    for d in docs:
        if not d.filename:
            continue
        doc_claimed.add(d.filename)
        if d.filename not in doc_files:
            doc_missing.append({"id": d.id, "title": d.title,
                                "filename": d.filename,
                                "trashed": int(d.is_trashed or 0)})
    doc_stray = sorted(doc_files - doc_claimed)

    out["documents"] = {
        "rows": len(docs),
        "files": len(doc_files),
        "missing_file": len(doc_missing),
        "stray_file": len(doc_stray),
        "samples": {"missing_file": doc_missing[:SAMPLE],
                    "stray_file": doc_stray[:SAMPLE]},
    }

    # ---- derived rows with no photo ---------------------------------------
    # PhotoVector went uncleaned for the whole life of the project before
    # purge_photos() was introduced, so any library older than that still
    # carries embeddings for photos that no longer exist. Faces are checked
    # the same way because the next table to be forgotten will be some other
    # one, and a count is how that gets noticed.
    photo_ids = {p.id for p in photos}
    orphan_vec = [v for (v,) in db.query(PhotoVector.photo_id)
                  .filter(PhotoVector.user_id == user_id).all()
                  if v not in photo_ids]
    orphan_face = [f for (f,) in db.query(PhotoFace.photo_id)
                   .filter(PhotoFace.user_id == user_id).all()
                   if f not in photo_ids]

    out["derived"] = {
        "orphan_vectors": len(orphan_vec),
        "orphan_faces": len(orphan_face),
        "samples": {"orphan_vectors": orphan_vec[:SAMPLE],
                    "orphan_faces": sorted(set(orphan_face))[:SAMPLE]},
    }

    out["clean"] = (out["gallery"]["missing_file"] == 0
                    and out["gallery"]["missing_thumb"] == 0
                    and out["gallery"]["stray_file"] == 0
                    and out["documents"]["missing_file"] == 0
                    and out["documents"]["stray_file"] == 0
                    and out["derived"]["orphan_vectors"] == 0
                    and out["derived"]["orphan_faces"] == 0)
    return out


def repair_derived(db: Session, user_id: int) -> dict:
    """Delete embeddings and faces belonging to photos that no longer exist.

    The ONLY repair offered here, and it is offered because it is the only one
    that cannot lose anything: a vector or a face embedding whose photo has
    been deleted is unreachable by every query in the application and can be
    recomputed from the photo if the photo ever comes back. Nothing a user
    could want is in it.

    Missing files and stray files are deliberately NOT repaired. A missing
    file may be an unplugged drive rather than lost data, and a stray file may
    be the last copy of something whose row was lost. Acting on either turns a
    recoverable situation into a permanent one.
    """
    photo_ids = {p for (p,) in db.query(GalleryPhoto.id)
                 .filter(GalleryPhoto.user_id == user_id).all()}

    vecs = [v for (v,) in db.query(PhotoVector.photo_id)
            .filter(PhotoVector.user_id == user_id).all() if v not in photo_ids]
    dead_faces = [fid for (fid, pid) in
                  db.query(PhotoFace.id, PhotoFace.photo_id)
                  .filter(PhotoFace.user_id == user_id).all()
                  if pid not in photo_ids]

    n_v = n_f = 0
    if vecs:
        n_v = (db.query(PhotoVector)
               .filter(PhotoVector.user_id == user_id,
                       PhotoVector.photo_id.in_(vecs))
               .delete(synchronize_session=False))
    if dead_faces:
        n_f = (db.query(PhotoFace)
               .filter(PhotoFace.id.in_(dead_faces))
               .delete(synchronize_session=False))
    if n_v or n_f:
        db.commit()
    return {"vectors_removed": int(n_v), "faces_removed": int(n_f)}
