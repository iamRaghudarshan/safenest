"""The simple answer to "back up my gallery automatically": watch a folder.

The screen this serves is deliberately three things — a folder, a switch, and a
count. Everything harder than that has already been tried and rejected by the
person it was for: picking files by hand does not scale past what an iPhone will
hand over, and building a shortcut on the phone works but is five minutes of
following instructions.

`check` exists so the two ways this fails are reported when the folder is typed,
not silently five minutes later in a background thread nobody is watching: a path
that does not exist, and one that exists but holds no photos.
"""
import os
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import autoimport, ist
from ..database import get_db
from ..helpers import audit
from ..models import AutoImport, User
from ..security import get_current_user

router = APIRouter(prefix="/api/autoimport", tags=["autoimport"])


def _row(r: AutoImport | None) -> dict:
    if not r:
        return {"folder": "", "enabled": False, "imported": 0, "skipped": 0,
                "last_scan_at": None, "last_error": ""}
    return {
        "folder": r.folder or "",
        "enabled": bool(r.enabled),
        "imported": int(r.imported or 0),
        "skipped": int(r.skipped or 0),
        "last_scan_at": ist.fmt(r.last_scan_at) if r.last_scan_at else None,
        "last_error": r.last_error or "",
    }


def _mine(db: Session, user: User) -> AutoImport | None:
    return db.query(AutoImport).filter(AutoImport.user_id == user.id).first()


@router.get("")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _row(_mine(db, user))


@router.post("/check")
def check(body: dict = Body(...), user: User = Depends(get_current_user)):
    """Does this folder exist, and does it have photos in it?

    Answered before saving, because a background job that quietly does nothing is
    indistinguishable from one that is working and has found nothing to do.
    """
    folder = (body.get("folder") or "").strip().strip('"')
    if not folder:
        raise HTTPException(422, "Type the folder to watch")
    p = Path(folder)
    if not p.is_dir():
        raise HTTPException(404, f"This computer cannot see a folder at {folder}")
    if not os.access(p, os.R_OK):
        raise HTTPException(403, f"{folder} cannot be read by {'the app'}")
    found = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if Path(name).suffix.lower() in autoimport.EXTS:
                found += 1
                if found >= 5000:
                    break
        if found >= 5000:
            break
    return {"ok": True, "folder": str(p), "photos": found}


@router.post("")
def save(request: Request, body: dict = Body(...),
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    folder = (body.get("folder") or "").strip().strip('"')
    enabled = bool(body.get("enabled"))
    if enabled and not folder:
        raise HTTPException(422, "Choose a folder before switching this on")
    if enabled and not Path(folder).is_dir():
        raise HTTPException(404, f"This computer cannot see a folder at {folder}")

    now = ist.now()
    row = _mine(db, user)
    if not row:
        row = AutoImport(user_id=user.id, imported=0, skipped=0, created_at=now)
        db.add(row)
    row.folder = folder
    row.enabled = 1 if enabled else 0
    row.last_error = ""
    row.updated_at = now
    db.commit()
    db.refresh(row)

    audit(db, user.id, "autoimport_save", "autoimport", row.id,
          {"label": folder or "(cleared)", "on": enabled}, request=request)
    if enabled:
        autoimport.start()
        autoimport.wake()   # so saving shows an effect now, not in five minutes
    return _row(row)
