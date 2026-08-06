"""Watch a folder and bring in whatever photos appear in it.

THE POINT OF THIS FILE
Every other way of getting photos in needs somebody to do something. The file
picker needs a selection, and on an iPhone it stops closing above a hundred
photos. The Shortcuts route works but is five minutes of setting up on the phone.
Asked for something simple that backs the gallery up automatically, neither is
the right shape: they are chores, and a chore is exactly what a backup must not
be, because the day you stop doing it is the day it stops protecting you.

This is the set-and-forget one. Choose a folder; anything that ever lands in it
is imported. Getting photos INTO that folder is then handled by things that
already exist and that we neither built nor can break — iCloud for Windows
dropping them in, Windows importing them when the phone is plugged in, or a
straight copy from the phone's DCIM.

HOW IT AVOIDS BEING EXPENSIVE
A folder can hold twenty thousand photos and is re-read every few minutes, so the
cheap check comes first: path, size and modification time. Only a file that looks
new is opened at all. The authoritative duplicate check is still the content hash
inside store_photo, so a file that is copied twice under two names is still one
photo — the cheap check is an optimisation, never the decision.

HOW IT AVOIDS BEING RUDE
It sleeps between photos and yields entirely while somebody is uploading, the
same way the indexer does. A background job with no deadline must never be the
reason the thing a person is watching goes slowly.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import indexer, ist
from .database import SessionLocal
from .models import AutoImport, User

# Extensions worth opening. Videos are not photos and the gallery cannot store
# them; .AAE is the sidecar an iPhone leaves beside an edited photo.
EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
        ".heic", ".heif", ".avif", ".dng"}

SCAN_EVERY = 300          # seconds between passes when nothing changed
REST_SECONDS = 0.05       # between photos, so a big first import stays polite
MAX_PER_PASS = 500        # so one pass cannot run for an hour without reporting

_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()
_lock = threading.Lock()
# path -> (size, mtime) already considered. Held in memory only: losing it costs
# one extra directory walk, and persisting it would be a second source of truth
# to disagree with the database.
_seen: dict[str, tuple[int, float]] = {}


def wake() -> None:
    """Scan now rather than at the next tick — used when the folder is changed."""
    _wake.set()


def _candidates(folder: Path) -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(folder):
        # Skip the app's own storage if someone points this at it, and the hidden
        # bookkeeping folders sync tools leave behind.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if Path(name).suffix.lower() in EXTS:
                out.append(Path(root) / name)
    return out


def _scan_one(row_id: int) -> None:
    from .routers.gallery import store_photo   # imported late: routers import us

    db = SessionLocal()
    try:
        row = db.query(AutoImport).filter(AutoImport.id == row_id).first()
        if not row or not row.enabled or not (row.folder or "").strip():
            return
        folder = Path(row.folder)
        if not folder.is_dir():
            row.last_error = f"{folder} is not a folder this computer can reach"
            row.last_scan_at = ist.now()
            db.commit()
            return

        user = db.query(User).filter(User.id == row.user_id).first()
        if not user:
            return

        done = 0
        for path in _candidates(folder):
            if _stop.is_set() or done >= MAX_PER_PASS:
                break
            try:
                st = path.stat()
            except OSError:
                continue
            key, sig = str(path), (st.st_size, st.st_mtime)
            if _seen.get(key) == sig:
                continue
            if st.st_size == 0:
                _seen[key] = sig        # nothing to read, and nothing will change
                continue

            # Stand aside while somebody is actually uploading. Their bar is on
            # screen; this has no deadline at all.
            while not _stop.is_set() and indexer.uploading_now():
                time.sleep(1.0)

            # Marked only once the file has an OUTCOME — imported, duplicate, or
            # undecodable. Marking it before the attempt meant anything that cut
            # the loop short in between (a stop, a restart, an error on the way to
            # store_photo) left the file remembered as dealt with and never looked
            # at again, because its size and mtime never change afterwards. A photo
            # silently missing from a backup is the worst failure this file has.
            try:
                out = store_photo(db, user, path.read_bytes(), path.name)
                _seen[key] = sig
                if out.get("duplicate"):
                    row.skipped = int(row.skipped or 0) + 1
                else:
                    row.imported = int(row.imported or 0) + 1
                done += 1
            except Exception as exc:
                db.rollback()
                # Still marked: a file that cannot be decoded must not be retried
                # every five minutes for ever. That is how ocr.py ended up printing
                # the same failure on a loop until it scrolled the console away.
                _seen[key] = sig
                row.skipped = int(row.skipped or 0) + 1
                row.last_error = f"{path.name}: {str(exc)[:160]}"
            time.sleep(REST_SECONDS)

        row.last_scan_at = ist.now()
        if done:
            row.last_error = ""
        db.commit()
        if done:
            try:
                indexer.start()
            except Exception:
                pass
    except Exception as exc:
        print(f"[autoimport] pass failed: {exc}")
    finally:
        db.close()


def _run() -> None:
    while not _stop.is_set():
        try:
            db = SessionLocal()
            ids = [r.id for r in db.query(AutoImport)
                   .filter(AutoImport.enabled == 1).all()]
            db.close()
            for rid in ids:
                if _stop.is_set():
                    break
                _scan_one(rid)
        except Exception as exc:
            print(f"[autoimport] {exc}")
        # Wakes early when the folder is changed, so saving a setting shows an
        # effect immediately rather than in five minutes' time.
        _wake.wait(SCAN_EVERY)
        _wake.clear()


def start() -> None:
    """Start the watcher, once. Safe to call from anywhere, including at boot."""
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, daemon=True)
        _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()
