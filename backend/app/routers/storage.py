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
