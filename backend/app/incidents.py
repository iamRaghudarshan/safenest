"""A short, durable record of things that went wrong.

WHY

The records-drive failure that this module comes out of left no trace at all.
Once the app was restarted there was nothing on the machine that said a drive had
failed, when, or how often -- so every question had to be answered by asking a
person to run commands, and "has this happened before?" could not be answered at
all. A fault that is invisible after the fact is a fault that gets diagnosed from
scratch every time.

WHERE IT IS WRITTEN

Beside the backups, in the INDEPENDENT location -- not in the records folder.
Deliberate: the incident most worth having a record of is the records folder
becoming unreadable, and a log kept inside it would be unreadable at exactly the
moment it was wanted.

WHAT IT IS NOT

Not telemetry. Nothing leaves the machine, ever. Not a debug log either -- one
line per real event, capped, so the file stays something a person can read to the
end rather than something that needs a tool.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

#: Keep the file small enough to read. At roughly 200 bytes a line this is a few
#: thousand events -- years of them for a healthy installation, and still bounded
#: for one that is failing every minute.
MAX_BYTES = 256 * 1024
KEEP_TAIL = 64 * 1024


def _path() -> Path | None:
    try:
        from . import backup
        return backup.backup_dir() / "incidents.log"
    except Exception:
        return None


def record(kind: str, detail: str, **extra) -> None:
    """Note that something happened. Never raises, never blocks anything.

    Every caller is on a path that is already dealing with a failure, so this
    cannot be allowed to add one of its own -- a logger that throws during an
    incident turns a recoverable fault into a crash, which is the exact shape of
    bug it exists to help diagnose.
    """
    try:
        p = _path()
        if p is None:
            return
        line = json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "detail": detail[:500],
            **{k: str(v)[:200] for k, v in extra.items()},
        }, ensure_ascii=False)
        if p.exists() and p.stat().st_size > MAX_BYTES:
            tail = p.read_bytes()[-KEEP_TAIL:]
            # Drop the partial first line so the file stays parseable line by line.
            p.write_bytes(tail[tail.find(b"\n") + 1:] if b"\n" in tail else b"")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def recent(limit: int = 50) -> list[dict]:
    """The last few events, newest first. Unparseable lines are skipped, not fatal."""
    try:
        p = _path()
        if p is None or not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return list(reversed(out))
    except OSError:
        return []


def note_startup_faults() -> None:
    """Record whatever the launcher found wrong, once, at startup.

    Called from main.py's startup rather than from the gate: the gate runs per
    request, and an unreadable drive would write a line for every poll the phone
    makes -- which is how a log becomes something nobody reads.
    """
    raw = (os.environ.get("SAFENEST_STORAGE_PROBLEM") or "").strip()
    if not raw:
        return
    try:
        p = json.loads(raw)
    except ValueError:
        p = {"reason": "unreadable"}
    record("storage", f"records folder unusable: {p.get('reason')}",
           volume=p.get("volume", ""), folder=p.get("folder", ""),
           mode=p.get("mode", ""))
