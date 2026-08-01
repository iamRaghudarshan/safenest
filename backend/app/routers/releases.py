"""Publishing a new version, and letting copies already out there fetch it.

THE PUBLISHER SIDE of updates.py. Two audiences, one file:

  * the operator, who builds a version and releases it to everyone, and
  * a customer's copy, which asks what the current version is and downloads it.

The customer-facing half is on `public`, alongside the licence check and the
announcements feed, because it is reached by copies that are not signed in to
this server and never will be. It is keyed on the licence id and refuses anyone
whose licence is not live: an update is part of what a licence pays for.
"""
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import bundler, ist, licensing, updates, weburl
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import Broadcast, License, Release, User
from ..ratelimit import rate_limit
from ..security import require_admin

router = APIRouter(prefix="/api/releases", tags=["releases"])
public = APIRouter(prefix="/api/licence", tags=["releases"])


def _require_publisher():
    if not settings.is_publisher:
        from .branding import app_name
        raise HTTPException(409, f"This copy of {app_name()} cannot publish "
                                 "releases — it has no signing key.")


def _releases_dir() -> Path:
    d = bundler.PROJECT_ROOT / "releases"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _row(r: Release) -> dict:
    return {
        "id": r.id,
        "version": r.version,
        "notes": r.notes or "",
        "filename": r.filename,
        "size_bytes": r.size_bytes or 0,
        "size_mb": round((r.size_bytes or 0) / 1048576, 1),
        "sha256": (r.sha256 or "")[:12],
        "platform": r.platform,
        "is_current": bool(r.is_current),
        "published_at": ist.fmt(r.published_at),
        "available": bool(r.path and Path(r.path).is_file()),
    }


@router.get("")
def listing(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Release).order_by(Release.id.desc()).all()
    live = db.query(License).filter(License.revoked_at.is_(None)).count()
    return {"releases": [_row(r) for r in rows],
            "running": updates.current_version(),
            "customers": live,
            "can_publish": settings.is_publisher}


@router.post("")
def publish(request: Request, body: dict = Body(...),
            admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Package the compiled build as a release and sign a manifest for it.

    Takes what is in dist-app right now, so the thing customers receive is the
    thing that was tested here — not a rebuild that happens to be running the same
    source. `bundler.compiled_available()` refuses if there is nothing built,
    rather than quietly publishing an empty archive.
    """
    _require_publisher()
    version = (body.get("version") or "").strip()
    notes = (body.get("notes") or "").strip()
    if not version:
        raise HTTPException(422, "Give it a version, like 2.1")
    if db.query(Release).filter(Release.version == version).first():
        raise HTTPException(409, f"Version {version} has already been published. "
                                 "Use a new number — copies decide by comparing "
                                 "it with what they are running.")
    if not bundler.compiled_available():
        raise HTTPException(409, "There is no compiled build to release. Run:\n"
                                 "  python packaging/build_exe.py --native")

    out = _releases_dir()
    stem = f"{bundler.current_app_name()}-{version}".replace(" ", "-")
    archive = out / f"{stem}.zip"
    if archive.exists():
        archive.unlink()

    # Zipped straight from the compiled folder. The customer's own data folder is
    # not in there — dist-app has no data/ — so an update cannot carry anyone's
    # records to anyone else.
    src = bundler.COMPILED_DIR
    shutil.make_archive(str(archive.with_suffix("")), "zip",
                        root_dir=str(src.parent), base_dir=src.name)

    sha = updates.digest(archive)
    size = archive.stat().st_size
    token, _payload = updates.sign(settings.license_signing_key_hex,
                                   version=version, sha256=sha, size=size,
                                   notes=notes, filename=archive.name)
    now = ist.now()
    rel = Release(version=version, notes=notes, filename=archive.name,
                  path=str(archive), size_bytes=size, sha256=sha, manifest=token,
                  platform="windows", is_current=0, published_at=now,
                  published_by=admin.id, created_at=now)
    db.add(rel)
    db.commit()
    db.refresh(rel)
    audit(db, admin.id, "release_publish", "release", rel.id,
          {"label": version, "size": size}, request=request)
    return _row(rel)


@router.post("/{release_id}/release-to-all")
def release_to_all(release_id: int, request: Request, body: dict = Body(default={}),
                   admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Make this the version every licensed copy is offered.

    Also posts an announcement, because the copies poll for those already and a
    person who is told "version 2.1 is ready" understands what the update prompt
    in their app is for. Silent is worse: an app that suddenly offers to replace
    itself, with no word from anyone, is exactly what people are taught to refuse.
    """
    _require_publisher()
    rel = db.query(Release).filter(Release.id == release_id).first()
    if not rel:
        raise HTTPException(404, "No such release")
    if not (rel.path and Path(rel.path).is_file()):
        raise HTTPException(409, "That release's file is missing from this "
                                 "computer, so nobody could download it.")

    db.query(Release).filter(Release.is_current == 1).update({"is_current": 0})
    rel.is_current = 1
    db.commit()

    told = 0
    if body.get("announce", True):
        now = ist.now()
        # Matches how every other broadcast is written — audience "all", and the
        # version in app_version, which is the column that exists for exactly this.
        msg = Broadcast(kind="update", audience="all",
                        app_version=rel.version[:20],
                        title=f"Version {rel.version} is available",
                        body=(rel.notes or "").strip()
                        or f"Open {bundler.current_app_name()} and choose "
                           "Check for updates to install it.",
                        created_at=now, created_by=admin.id)
        db.add(msg)
        db.commit()
        told = db.query(License).filter(License.revoked_at.is_(None)).count()

    audit(db, admin.id, "release_to_all", "release", rel.id,
          {"label": rel.version, "customers": told}, request=request)
    return {**_row(rel), "announced_to": told}


@router.delete("/{release_id}")
def withdraw(release_id: int, request: Request,
             admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Stop offering a release. The file and the record are both kept."""
    rel = db.query(Release).filter(Release.id == release_id).first()
    if not rel:
        raise HTTPException(404, "No such release")
    rel.is_current = 0
    db.commit()
    audit(db, admin.id, "release_withdraw", "release", rel.id,
          {"label": rel.version}, request=request)
    return _row(rel)


# ------------------------------------------------- what a customer's copy asks
def _live_licence(key_id: str, db: Session) -> License:
    lic = db.query(License).filter(License.key_id == key_id).first()
    if not lic:
        raise HTTPException(404, "Unknown licence")
    if lic.revoked_at:
        raise HTTPException(403, "This licence has been withdrawn.")
    return lic


@public.get("/update/{key_id}")
def offered(key_id: str, request: Request, db: Session = Depends(get_db)):
    """The current release, as a signed manifest. Called by customers' copies.

    Returns the manifest and a download address, and nothing else. The copy checks
    the signature itself; this endpoint being wrong, or impersonated, cannot make
    it install anything.
    """
    rate_limit(request, "update-check", limit=60, window=60)
    _live_licence(key_id, db)
    rel = db.query(Release).filter(Release.is_current == 1).first()
    if not rel:
        return {"available": False}
    base = weburl.public_url(db).rstrip("/")
    return {
        "available": True,
        "version": rel.version,
        "notes": rel.notes or "",
        "size_bytes": rel.size_bytes or 0,
        "manifest": rel.manifest,
        "url": f"{base}/api/licence/download/{key_id}",
    }


@public.get("/download/{key_id}")
def download(key_id: str, request: Request, db: Session = Depends(get_db)):
    """The release file itself."""
    rate_limit(request, "update-download", limit=6, window=600)
    _live_licence(key_id, db)
    rel = db.query(Release).filter(Release.is_current == 1).first()
    if not rel or not (rel.path and Path(rel.path).is_file()):
        raise HTTPException(404, "No release is available")
    return FileResponse(rel.path, filename=rel.filename,
                        media_type="application/zip")
