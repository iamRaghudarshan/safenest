"""Getting back in when the records folder cannot be read.

THE FAILURE THIS EXISTS FOR
Records may live somewhere other than the default folder -- an external disk,
usually. When that place stops answering, everything downstream of it fails at
once and none of the failures name the cause: the licence file cannot be read, so
the app reports no licence; the database cannot be read, so every request is a
500. A customer looking at that has no way to reach the truth, which is that a USB
drive is faulty. They reinstall, because reinstalling is what you do when an app
is broken -- and on a records app that is the one action that can turn a
recoverable situation into a real loss.

So the launcher probes the folder before using it (runner.py::_probe), the gate in
main.py refuses the rest of the API while it is unusable, and these three
endpoints are what the recovery screen calls. Between them they cover every way
out that does not involve a terminal:

    problem    what is wrong, in a sentence, plus the paths involved
    retry      the drive is back -- check, and use it again
    use-local  it is not coming back today -- work from this computer meanwhile

WHAT THIS DELIBERATELY WILL NOT DO
Delete the pointer. It is the only record of where the records actually are, and
a customer who cannot read their drive today may well read it tomorrow on another
machine. `use-local` moves it aside, keeping the path inside the file it writes,
so the choice is reversible by hand and reversible from the screen.
"""
import ipaddress
import json
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/storage", tags=["storage"])

#: Same name the launcher sets. Duplicated rather than imported: `packaging` is
#: not on the path in a bundled copy, and a missing import here would take the
#: whole server down -- on the one code path whose entire job is to come up when
#: something else has failed.
PROBLEM_ENV = "SAFENEST_STORAGE_PROBLEM"

#: Where a moved-aside pointer goes, next to the original.
PARKED = "data-location.unavailable.txt"


def _problem() -> dict | None:
    raw = (os.environ.get(PROBLEM_ENV) or "").strip()
    if not raw:
        return None
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except ValueError:
        return {"reason": "unreadable"}


def _at_the_machine(request: Request) -> None:
    """Refuse anything but a request from this computer itself.

    Repointing where records are read from is not a remote operation. Someone on
    the LAN, or through the tunnel, cannot plug a drive in or judge whether the
    right one is attached -- and they should not be able to move a household's
    records onto a different folder from another room. Loopback only, which is
    stricter than the LAN check hosting.py uses, on purpose.
    """
    host = (request.client.host if request.client else "").strip()
    try:
        if host and ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise HTTPException(403, "This can only be done on the computer itself.")


@router.get("/problem")
def problem():
    """What is wrong with the records folder, if anything.

    Open on purpose, and readable from the phone: the phone is very often where
    somebody first notices, and "your computer's records drive is not readable"
    is a far better thing for it to show than a licence error it cannot act on.
    It exposes a folder path and nothing else -- no records, no credentials.
    """
    p = _problem()
    if p is None:
        return {"ok": True}
    return {"ok": False, **p}


@router.post("/retry")
def retry(request: Request):
    """The drive is back. Check, and if it reads, use it again.

    Re-probes rather than trusting the person pressing it. A drive that mounts
    but errors is exactly the case that started all of this, and it looks
    connected to everybody including its owner.
    """
    _at_the_machine(request)
    p = _problem()
    if p is None:
        return {"ok": True, "restart_required": False}
    folder = Path(p.get("folder") or "")
    if not folder.name:
        raise HTTPException(409, "There is no records folder recorded to retry.")
    fault = _probe(folder)
    if fault is not None:
        return {"ok": False, "restart_required": False, **fault}
    # Cleared. The database engine was bound to the fallback folder at startup, so
    # the app has to come up again to actually use this one -- saying otherwise
    # would show a working screen still reading the wrong folder.
    os.environ.pop(PROBLEM_ENV, None)
    return {"ok": True, "restart_required": True}


@router.post("/use-local")
def use_local(request: Request):
    """Work from this computer for now, without losing where the records are.

    The pointer is moved, not deleted, and the path it held is written into the
    file that replaces it. Nothing on the unreadable drive is touched -- this end
    of it cannot reach that drive anyway, which is the entire problem.
    """
    _at_the_machine(request)
    p = _problem()
    if p is None:
        return {"ok": True, "restart_required": False}
    pointer = Path(p.get("pointer") or "")
    if not pointer.name:
        raise HTTPException(409, "There is no records location file to move.")
    parked = pointer.with_name(PARKED)
    try:
        if pointer.is_file():
            shutil.move(str(pointer), str(parked))
    except OSError as exc:
        raise HTTPException(500, f"Could not move {pointer}: {exc}")
    os.environ.pop(PROBLEM_ENV, None)
    return {"ok": True, "restart_required": True,
            "kept_at": str(parked), "was": p.get("folder", "")}


def _probe(folder: Path) -> dict | None:
    """The launcher's probe, repeated here for the reason PROBLEM_ENV is.

    Kept deliberately identical in behaviour to runner.py::_probe. If the two ever
    disagree, the one that decides is the launcher's -- this one only decides
    whether it is worth asking the customer to restart.
    """
    try:
        if not folder.is_dir():
            return {"reason": "missing", "folder": str(folder)}
        os.listdir(folder)
    except OSError as exc:
        return {"reason": "unreadable", "folder": str(folder), "detail": str(exc)}
    marker = folder / ".write-test"
    try:
        marker.write_text("ok", encoding="utf-8")
        marker.read_text(encoding="utf-8")
    except OSError as exc:
        return {"reason": "readonly", "folder": str(folder), "detail": str(exc)}
    finally:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            pass
    return None


# --------------------------------------------------------------- self-diagnosis
#
# WHY THIS IS HERE AND NOT IN A RUNBOOK
#
# The incident this file exists for cost most of a day, and almost none of that
# was the bug. The bug was found in minutes once somebody ran `ls` on the right
# folder. What it cost was the RELAY: every fact had to be fetched by asking a
# person to open Terminal, paste a command, and send back what it printed --
# through a second person, on a machine nobody supporting them could see.
#
# That does not scale past one customer, and it puts the one action that can turn
# a recoverable fault into permanent loss -- reinstall, reformat -- in the hands
# of somebody who has been given no way to find out what is actually wrong.
#
# So every check that was run by hand that day is run by the app instead, on
# demand, and answered in sentences. The next incident starts from a report
# rather than a phone call.
import platform
import shutil
import sqlite3
from datetime import datetime, timezone

from fastapi import Depends

from ..config import settings
from ..models import User
from ..security import get_current_user


def _check(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    """One answered question. ok=None means "cannot tell", which is not "fine"."""
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def _records_dir() -> Path:
    """The folder the running server is actually using.

    settings.sqlite_path, not a path re-derived from the pointer file: the point
    of a diagnostic is to report what IS, and a fallback launch is precisely the
    case where the two answers differ. Reporting the intended folder there would
    make the diagnostic agree with the assumption instead of the machine.
    """
    try:
        if settings.is_sqlite:
            return settings.sqlite_path.parent
    except Exception:
        pass
    return Path.cwd()


@router.get("/diagnose")
def diagnose(user: User = Depends(get_current_user)):
    """Everything support would otherwise ask somebody to type into a terminal.

    Signed in, not admin: the person facing a broken app is whoever is sitting at
    it, and a fault that stops the app is not a moment to also discover that your
    account cannot ask about it. Nothing here is a secret -- folder paths, sizes,
    dates and yes/no answers.
    """
    out: list[dict] = []

    # 1. The fault that started all this, first, because it causes the others.
    p = _problem()
    if p is None:
        out.append(_check("Records location", True,
                          "The folder your records are kept in is readable."))
    else:
        where = p.get("volume") or p.get("folder") or "another disk"
        mode = p.get("mode")
        out.append(_check(
            "Records location", False,
            f"Your records are kept on {where}, which cannot be "
            f"{'found' if p.get('reason') == 'missing' else 'read'}."
            + (" SafeNest is working from the copy on this computer meanwhile."
               if mode == "fallback" else ""),
            "Reconnect that drive, or use Try again on the records screen. "
            "Nothing has been deleted."))

    # 2. Does the database open, and does it hold anything? "Opens" is not the
    #    same question as "has records in it", and the second is the one somebody
    #    means when they say the app came up empty.
    data = _records_dir()
    # NOT an early return. The licence, free space and backup checks below apply
    # whatever the database is, and skipping them on one installation shape is how
    # a diagnostic quietly stops covering the machine it is run on.
    if not settings.is_sqlite:
        out.append(_check("Your records", None,
                          "This installation keeps records in MySQL, not a file, "
                          "so it is not checked from here.", ""))
    else:
      db = settings.sqlite_path
      try:
          size = db.stat().st_size if db.is_file() else 0
          con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
          try:
              users = con.execute("SELECT count(*) FROM users").fetchone()[0]
          finally:
              con.close()
          out.append(_check("Your records", users > 0,
                            f"{users} account(s), database {size // 1048576} MB, at {data}",
                            "" if users else
                            "This database is empty. If you expected records here, do "
                            "NOT reinstall — the records are likely in another folder."))
      except (OSError, sqlite3.Error) as exc:
          out.append(_check("Your records", False, f"Cannot read {db}: {exc}",
                            "Do not reinstall. Send this report."))

    # 3. Licence -- reported as its own line so it is never again the symptom
    #    somebody chases when the real fault is the disk under it.
    # A publisher copy HAS no licence -- licensed_mode is what the bundler sets on
    # copies handed to other people -- so reporting one missing here is a false
    # alarm, and false alarms are what teach somebody to ignore the real one.
    if not settings.licensed_mode:
        out.append(_check("Licence", None,
                          "This copy does not use a licence file.", ""))
    else:
      try:
          from .. import licensing
          st = licensing.cached_status(settings.license_path,
                                       settings.license_public_key_hex)
          kind = st.get("state", "unknown")
          out.append(_check("Licence", kind not in ("missing", "invalid", "expired"),
                            f"{kind}" + (f", expires {st.get('expires_on')}"
                                         if st.get("expires_on") else ""),
                            "" if kind != "missing" else
                            "If the records location above is also failing, fix that "
                            "first — the licence file lives in that folder."))
      except Exception as exc:
          out.append(_check("Licence", None, f"Could not be checked: {exc}"))

    # 4. Room to work. A full disk fails in ways that look like anything else.
    try:
        free = shutil.disk_usage(data).free
        out.append(_check("Free space", free > 512 * 1024 * 1024,
                          f"{free // 1073741824} GB free where your records are",
                          "" if free > 512 * 1024 * 1024 else
                          "Under 512 MB free. Saving will start failing."))
    except OSError as exc:
        out.append(_check("Free space", None, str(exc)))

    # 5. When was the last copy taken? The question every incident ends on.
    #
    # ASKS backup.py, and does not go looking for files itself. The first version
    # of this globbed data/finmate.db.bak-* -- which is where the pre-migration
    # snapshot goes, not where the real backups live (backup_dir(), an INDEPENDENT
    # location outside the records folder, deliberately, so a lost records disk
    # does not take the backups with it). It would have reported "no backup found"
    # on every healthy installation in existence. A diagnostic that invents faults
    # is worse than none: it is what teaches somebody to ignore the true one.
    try:
        from .. import backup as _bk
        newest = _bk.newest_good()
        if newest is None:
            out.append(_check("Last backup", False, "No usable backup was found",
                              "Take one now from Profile → Backup."))
        else:
            when = datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
            age = (datetime.now(timezone.utc) - when).days
            out.append(_check(
                "Last backup", age <= 7,
                f"{age} day(s) old, {newest.stat().st_size // 1048576} MB, "
                f"kept at {newest.parent}",
                "" if age <= 7 else
                "Older than a week. The app takes one daily while it is running, "
                "so this old usually means it has not been opened."))
    except Exception as exc:
        out.append(_check("Last backup", None, f"Could not be checked: {exc}"))

    # 6. Is the database itself sound? backup.py already checks this at startup
    #    and restores from the newest good copy if not -- reported here so the
    #    answer is visible rather than only acted on.
    try:
        from .. import backup as _bk
        out.append(_check("Database integrity", _bk.integrity_ok(),
                          "SQLite reports the records file as sound"
                          if _bk.integrity_ok() else
                          "SQLite reports damage in the records file",
                          "" if _bk.integrity_ok() else
                          "SafeNest restores from the newest good backup on the "
                          "next start. Do not reinstall."))
    except Exception as exc:
        out.append(_check("Database integrity", None, f"Could not be checked: {exc}"))

    return _finish(out, data)


def _finish(out: list[dict], data: Path) -> dict:
    from .. import updates
    return {
        "app": {"version": updates.current_version(),
                "platform": platform.platform(),
                "records_at": str(data)},
        "checks": out,
        # None means "could not tell", and that must not read as passing. Only
        # answered checks count, and an unanswered one is reported as itself.
        "all_ok": all(c["ok"] for c in out if c["ok"] is not None),
        "unchecked": [c["name"] for c in out if c["ok"] is None],
        # "Has this happened before?" -- the question that could not be answered
        # at all during the incident this came out of.
        "history": _history(),
    }


def _history() -> list[dict]:
    try:
        from .. import incidents
        return incidents.recent(20)
    except Exception:
        return []
