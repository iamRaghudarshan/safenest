"""Automatic rolling backups + corruption detection for the SQLite database.

Records may live on an external drive now (see bundle/wizard.py). An external drive
can fail, be lost, or be corrupted by a bad unplug in a way no in-process guard
prevents. So the app keeps its own copies on the computer's INTERNAL disk,
independent of wherever the records live, taken with SQLite's online backup API — a
consistent snapshot of exactly what is committed, safe to run while the app is
live. A new snapshot is verified (it must open and pass an integrity check) before
any old one is pruned, so a bad backup never evicts a good one.

On startup the live database is integrity-checked; if it is corrupt AND a good
backup exists, the corrupt file is set aside (never deleted) and the newest good
backup is restored in its place — the alternative is serving a broken or blank
database over someone's real records. Everything here is best-effort and wrapped so
a backup problem can never stop the app from starting.

Scope: the database only, not the media. Photos and documents are large and live on
the data drive; the database is the small, irreplaceable, structured half (every
record, plus the AES-encrypted vault whose key lives with the app on the internal
disk — so backup + key recovers the vault). MySQL (the publisher install) is
skipped: it is not a single file and has its own backup story; this addresses the
fragile single-file case a customer on removable storage actually faces.
"""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from . import ist
from .config import settings

KEEP = 14              # how many rolling snapshots to retain (~two weeks, daily)
MIN_INTERVAL_H = 20    # do not take another automatic backup within this many hours


def _enabled() -> bool:
    return settings.is_sqlite


def _live_db() -> Path:
    return settings.sqlite_path


def safe_root() -> Path:
    """An internal-disk folder independent of wherever the records live.

    Deliberately NOT beside the database: the database may be on the very external
    drive whose failure this protects against, so a copy next to it is no protection
    at all. The OS per-user app-data area is on the internal disk on every normal
    setup. A sibling of any data folder (not a child), so it is never swept up by a
    "move to another computer" that copies the data directory.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "SafeNest Backups"


def backup_dir() -> Path:
    d = safe_root()
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        # The independent location is unavailable (odd permissions, no home). Fall
        # back to a folder beside the database — same drive, so weaker against a
        # disk failure, but a copy still survives an accidental wipe or a bad update.
        alt = _live_db().parent / "backups"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def _list() -> list[Path]:
    try:
        return sorted(backup_dir().glob("finmate-*.db"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return []


def verify(path: Path) -> bool:
    """Does this file open as SQLite and pass a quick integrity check?"""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def integrity_ok(path: Path | None = None) -> bool:
    """True if the (live, by default) database is present and not corrupt.

    A MISSING file is treated as OK: that is a fresh install, which create_all()
    handles — restoring a backup over a first run would be wrong. Only an existing
    file that fails the check counts as corrupt.
    """
    p = path or _live_db()
    if not p.is_file():
        return True
    return verify(p)


def run_backup(reason: str = "auto") -> str:
    """Take one online backup, verify it, prune old ones. Returns the path or "".

    Never raises. Safe to call while the app is serving requests: the backup API
    reads a consistent committed snapshot without blocking writers under WAL.
    """
    if not _enabled():
        return ""
    live = _live_db()
    if not live.is_file():
        return ""
    stamp = ist.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir() / f"finmate-{stamp}.db"
    try:
        src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        print(f"[backup] could not snapshot the database: {exc}")
        dest.unlink(missing_ok=True)   # a half-written snapshot is worse than none
        return ""
    if not verify(dest):
        print("[backup] snapshot failed its integrity check - discarded")
        dest.unlink(missing_ok=True)
        return ""
    _prune()
    print(f"[backup] saved {dest.name} ({reason}) in {dest.parent}")
    return str(dest)


def _prune() -> None:
    """Keep the newest KEEP snapshots; delete older ones. Runs after a verified
    snapshot, so the set being trimmed always contains at least one good copy."""
    items = _list()
    for old in items[:-KEEP] if len(items) > KEEP else []:
        try:
            old.unlink()
        except OSError:
            pass


def newest_good() -> Path | None:
    for p in reversed(_list()):
        if verify(p):
            return p
    return None


def maybe_backup(reason: str = "auto") -> str:
    """Back up only if the newest snapshot is older than MIN_INTERVAL_H.

    Called on startup and each scheduler tick; the age check keeps it to about once
    a day without a stored timestamp to keep in sync, and makes the scheduler call
    a cheap stat on every tick but a real copy only once daily.
    """
    if not _enabled():
        return ""
    items = _list()
    if items:
        age_h = (ist.now().timestamp() - items[-1].stat().st_mtime) / 3600.0
        if age_h < MIN_INTERVAL_H:
            return ""
    return run_backup(reason)


def ensure_healthy_or_restore() -> None:
    """Startup guard: if the live database is corrupt, restore the newest good backup.

    Never raises. Does nothing when the database is fine or simply absent (a fresh
    install). When it is corrupt, the broken file is moved aside for forensics —
    never deleted — and the newest verified backup is copied into its place. With no
    backup to restore from it leaves everything alone and only warns: silently
    replacing a broken file with a blank one is exactly what must not happen to
    someone's records.

    Call this BEFORE the first SQLAlchemy use (before _migrate) so no pooled
    connection holds the file open while it is swapped.
    """
    if not _enabled():
        return
    live = _live_db()
    try:
        if integrity_ok(live):
            return
        good = newest_good()
        if not good:
            print(f"[backup] the database at {live} looks corrupt and there is no "
                  f"backup to restore - leaving it untouched.")
            return
        stamp = ist.now().strftime("%Y%m%d-%H%M%S")
        aside = live.with_name(f"{live.stem}-corrupt-{stamp}{live.suffix}")
        # Move the broken db and its journal aside — never delete customer data.
        for suf in ("", "-wal", "-shm"):
            f = live.with_name(live.name + suf)
            if f.is_file():
                try:
                    f.rename(aside.with_name(aside.name + suf) if suf else aside)
                except OSError:
                    pass
        shutil.copy2(good, live)
        print(f"[backup] the database was corrupt - restored {good.name}. The "
              f"unreadable copy was kept as {aside.name}.")
    except Exception as exc:
        print(f"[backup] health check skipped: {exc}")
