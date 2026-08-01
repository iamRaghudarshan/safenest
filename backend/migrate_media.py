"""One-off migration: flat media folders -> per-user, per-variant directories.

    BEFORE
      backend/uploads/gallery/<uuid>.jpg
      backend/uploads/gallery/thumb_<uuid>.jpg
      backend/private/documents/<uuid>.<ext>
      backend/private/documents/thumb_<uuid>.<ext>.jpg

    AFTER
      backend/private/gallery/<user_id>/original/<uuid>.jpg
      backend/private/gallery/<user_id>/thumb/<uuid>.jpg
      backend/private/documents/<user_id>/original/<uuid>.<ext>
      backend/private/documents/<user_id>/thumb/<uuid>.<ext>.jpg

Owner comes from the database row that references each file. Safe to re-run:
files already in place are skipped, and anything with no matching row is left
where it is and reported as an orphan rather than deleted.

    python migrate_media.py            # dry run — shows the plan, changes nothing
    python migrate_media.py --apply    # perform the moves
"""
import os
import shutil
import sys

from app import storage
from app.database import SessionLocal
from app.models import Document, GalleryPhoto

BACKEND = os.path.dirname(os.path.abspath(__file__))
OLD_GALLERY = os.path.join(BACKEND, "uploads", "gallery")
OLD_DOCS = os.path.join(BACKEND, "private", "documents")

APPLY = "--apply" in sys.argv


def plan_gallery(db):
    """(source, destination) pairs for every gallery file still in the flat folder."""
    moves, missing = [], []
    for p in db.query(GalleryPhoto).all():
        if not p.filename:
            continue
        for variant, old_name in ((storage.ORIGINAL, p.filename), (storage.THUMB, f"thumb_{p.filename}")):
            dest = storage.media_path(storage.GALLERY, p.user_id, variant, p.filename)
            if os.path.isfile(dest):
                continue  # already migrated
            src = os.path.join(OLD_GALLERY, old_name)
            if os.path.isfile(src):
                moves.append((src, dest))
            else:
                missing.append(old_name)
    return moves, missing


def plan_documents(db):
    moves, missing = [], []
    for d in db.query(Document).all():
        if not d.filename:
            continue
        pairs = [(storage.ORIGINAL, d.filename, d.filename)]
        if d.has_thumb:
            pairs.append((storage.THUMB, f"thumb_{d.filename}.jpg", f"{d.filename}.jpg"))
        for variant, old_name, new_name in pairs:
            dest = storage.media_path(storage.DOCUMENTS, d.user_id, variant, new_name)
            if os.path.isfile(dest):
                continue
            src = os.path.join(OLD_DOCS, old_name)
            if os.path.isfile(src):
                moves.append((src, dest))
            else:
                missing.append(old_name)
    return moves, missing


def orphans(moves):
    """Files sitting in the old flat folders that no database row claims."""
    claimed = {os.path.normcase(s) for s, _ in moves}
    out = []
    for folder in (OLD_GALLERY, OLD_DOCS):
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            if entry.is_file() and os.path.normcase(entry.path) not in claimed:
                out.append(entry.path)
    return out


def run():
    db = SessionLocal()
    try:
        g_moves, g_missing = plan_gallery(db)
        d_moves, d_missing = plan_documents(db)
    finally:
        db.close()

    moves = g_moves + d_moves
    stray = orphans(moves)

    print(f"gallery   : {len(g_moves):>6} file(s) to move, {len(g_missing)} referenced but not found on disk")
    print(f"documents : {len(d_moves):>6} file(s) to move, {len(d_missing)} referenced but not found on disk")
    print(f"unclaimed : {len(stray):>6} file(s) in the old folders with no database row (left untouched)")

    if not APPLY:
        print("\nDRY RUN — nothing changed. Re-run with --apply to perform the moves.")
        for s, d in moves[:5]:
            print(f"  {os.path.relpath(s, BACKEND)}  ->  {os.path.relpath(d, BACKEND)}")
        if len(moves) > 5:
            print(f"  ... and {len(moves) - 5} more")
        return

    done = 0
    for src, dest in moves:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        done += 1
        if done % 1000 == 0:
            print(f"  moved {done}/{len(moves)}...")
    print(f"\nMoved {done} file(s).")

    # Drop the old gallery tree only once it is genuinely empty.
    for folder in (OLD_GALLERY, OLD_DOCS):
        if os.path.isdir(folder) and not any(os.scandir(folder)):
            os.rmdir(folder)
            print(f"Removed empty {os.path.relpath(folder, BACKEND)}")
    uploads = os.path.join(BACKEND, "uploads")
    if os.path.isdir(uploads) and not any(os.scandir(uploads)):
        os.rmdir(uploads)
        print("Removed empty uploads/")


if __name__ == "__main__":
    run()
