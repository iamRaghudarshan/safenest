"""App maintenance: build info, CDN cache purging, and exporting the whole app to
another computer.

CDN purging exists because a stale edge copy of the service worker can pin every
phone to an old build, and the fix otherwise means logging into the Cloudflare
dashboard. The export builds a USB-ready copy of the app from the phone, so moving
to a new machine doesn't require sitting at the old one. Both are admin-only and
audited.
"""
import json
import os
import threading
import time

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from sqlalchemy import text

from .. import bundler, hosts, ist, push, storage, weburl
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import AuditLog, License, User
from ..ratelimit import rate_limit
from ..security import get_current_user, require_admin

router = APIRouter(prefix="/api/system", tags=["system"])

APP_VERSION = "2.0"

# Files that are NOT content-hashed and therefore the only ones a CDN can serve
# stale. /assets/* carry a hash in the filename, so they are safe to leave cached.
def _purge_targets(db: Session) -> list[str]:
    base = weburl.public_url(db)
    return [f"{base}/", f"{base}/index.html", f"{base}/sw.js", f"{base}/manifest.webmanifest"]


@router.get("/status")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What the Profile screen needs to render its maintenance section.

    Takes a session because the purge targets are built from the public address,
    which now lives in the database rather than in .env.
    """
    return {
        "version": APP_VERSION,
        "cdnPurge": {
            "available": settings.cdn_purge_enabled,
            "isAdmin": user.role == "admin",
            "targets": len(_purge_targets(db)),
        },
    }


def _database_bytes(db: Session) -> int:
    """How much room the records themselves take.

    Separate from the media total because they behave differently: photos grow
    with what you upload, the database grows with what you record, and only one
    of them is ever the surprise.
    """
    if settings.is_sqlite:
        total = 0
        main = settings.sqlite_path
        for path in (main, main.with_name(main.name + "-wal"),
                     main.with_name(main.name + "-shm")):
            if path.exists():
                total += path.stat().st_size
        return total
    try:
        return int(db.execute(text(
            "SELECT COALESCE(SUM(data_length + index_length), 0) "
            "FROM information_schema.tables WHERE table_schema = DATABASE()"
        )).scalar() or 0)
    except Exception:
        return 0


@router.get("/storage")
def storage_usage(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """How much room the app is taking on the computer that runs it.

    Everyone sees their own total. Admins additionally see the whole server and
    what's left on the drive — the number that decides whether the next batch of
    photos will fit.
    """
    mine = storage.usage_for(user.id)
    out = {"mine": mine, "server": None, "database": None, "disk": None}
    if user.role == "admin":
        out["server"] = storage.usage_everyone()
        out["database"] = _database_bytes(db)
        out["disk"] = storage.disk_space()
    return out


@router.get("/host")
def host_info(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Which computer is serving this, and which ones served it before.

    Visible to everyone, not just admins: when the app has moved and someone's
    records look wrong, the person who notices is whoever happened to open it.
    """
    rows = hosts.history(db)
    current = next((r for r in rows if r["is_current"]), None)
    if current is None:
        # First request after an upgrade, before any restart recorded a host.
        info = hosts.describe(db)
        current = {**info, "id": None, "app_version": APP_VERSION,
                   "first_seen": None, "last_seen": None, "is_current": True}
        current.pop("fingerprint", None)
    return {"current": current, "history": rows, "moves": max(0, len(rows) - 1)}


@router.post("/purge-cdn")
def purge_cdn(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Drop the CDN's copy of the non-hashed files so a new build reaches devices
    immediately instead of waiting out the edge TTL."""
    rate_limit(request, "purge-cdn", limit=5, window=300)
    if not settings.cdn_purge_enabled:
        raise HTTPException(501, "CDN purging isn't configured. Add CF_API_TOKEN and "
                                 "CF_ZONE_ID to backend/.env, then restart.")
    files = _purge_targets(db)
    try:
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/zones/{settings.cf_zone_id}/purge_cache",
            headers={"Authorization": f"Bearer {settings.cf_api_token}",
                     "Content-Type": "application/json"},
            json={"files": files}, timeout=15,
        )
        body = r.json()
    except requests.RequestException as e:
        raise HTTPException(502, f"Could not reach Cloudflare: {e}")

    if not r.ok or not body.get("success"):
        detail = "; ".join(m.get("message", "") for m in body.get("errors", [])) or r.text[:200]
        raise HTTPException(502, f"Cloudflare rejected the purge: {detail}")

    audit(db, admin.id, "purge_cdn", "system", None, {"files": len(files)}, request=request)
    return {"purged": files, "count": len(files)}


# ---------------------------------------------------------------- export bundle
# Building a bundle copies thousands of files and takes minutes, far longer than a
# phone will hold a request open. It runs on a worker thread and the app polls this
# small piece of state. One export at a time — a second would fight over the same
# output folder, and there is no sensible reason to run two.
_export = {"state": "idle", "step": "", "percent": 0, "started_at": None,
           "finished_at": None, "result": None, "error": None, "by": None,
           "owner_id": None, "scope": None}
_export_lock = threading.Lock()

# Longer than any genuine build (the largest so far is a couple of minutes), short
# enough that a crashed job doesn't block exports for the rest of the day.
STALE_AFTER = 30 * 60


def _notify_export_done(db: Session, user_id: int, ok: bool, result: dict, error: str | None):
    """Tell the person their build finished. They started it from a phone and were
    told they could walk away, so something has to reach them when it's ready."""
    from .branding import app_name
    brand = app_name(db)
    if ok:
        path = result.get("zip") or result.get("folder") or ""
        size = result.get("zip_bytes") or result.get("bytes") or 0
        payload = {
            "title": f"Your {brand} copy is ready",
            "body": f"{os.path.basename(path)} · {size / 1048576:.0f} MB — "
                    f"on the {brand} computer, ready to copy to a USB drive.",
        }
    else:
        payload = {"title": f"{brand} export failed",
                   "body": (error or "Something went wrong.")[:180]}
    try:
        # notify() stores it in the app first, then attempts the push — so the
        # alert is there when they open the app even if the push never lands.
        push.notify(db, user_id, payload["title"], payload["body"],
                    url="/", kind="export")
    except Exception as exc:  # a failed notification must not fail the export
        print(f"[export] could not notify user {user_id}: {exc}")


def _run_export(platform: str, include_data: bool, user_id: int, scope: str,
                notify: bool = False, licence_token: str = "", licence_tag: str = "",
                hosting: dict | None = None):
    def progress(step: str, percent: int):
        _export.update(step=step, percent=percent)

    try:
        if licence_token:
            # A customer's copy always goes out as the compiled build. The source
            # bundle is for moving your OWN installation between your OWN machines;
            # sending it to a customer would hand over every line of the code.
            result = bundler.build_licensed(
                platform, licence_token, progress=progress,
                folder_suffix=f"-{licence_tag}" if licence_tag else "",
                hosting=hosting)
        elif bundler.installed_root() is not None:
            # A customer's copy has no source tree to build from, so it clones the
            # installation instead. Without this, export failed there with
            # "frontend/dist is missing" — the one way records get out of the app,
            # broken for exactly the people who cannot rebuild it themselves.
            result = bundler.build_installed(
                platform, include_data, progress=progress,
                user_id=user_id if scope == "mine" else None,
                folder_suffix=f"-{user_id}" if scope == "mine" else "")
        else:
            result = bundler.build(
                platform, include_data, progress=progress,
                # A personal export is scoped to the caller and lands in its own
                # folder, so it can't overwrite (or be mistaken for) the full copy.
                user_id=user_id if scope == "mine" else None,
                folder_suffix=f"-{user_id}" if scope == "mine" else "",
                hosting=hosting)
        _export.update(state="done", result=result, percent=100, step="Done")
    except Exception as exc:
        # The message reaches a phone screen, so keep it short and human.
        _export.update(state="error", error=str(exc)[:300], step="Failed")
    finally:
        _export["finished_at"] = time.time()
        from ..database import SessionLocal
        db = SessionLocal()
        try:
            done = _export.get("result") or {}
            # The audit row doubles as the export history the app shows, so it keeps
            # enough to identify the copy on disk long after the job state is gone.
            audit(db, user_id, "export_bundle", "system", None, {
                "platform": platform, "with_data": include_data, "scope": scope,
                "state": _export["state"], "error": _export.get("error"),
                "folder": done.get("folder"), "zip": done.get("zip"),
                "bytes": done.get("zip_bytes") or done.get("bytes"),
                "media_files": done.get("media_files"),
                # Recorded so "why didn't I get a notification?" is answerable
                # from the log rather than by guesswork.
                "notify": notify,
            })
            if notify:
                _notify_export_done(db, user_id, _export["state"] == "done", done,
                                    _export.get("error"))
        finally:
            db.close()


def _visible_job(user: User) -> dict:
    """The job as this caller may see it. Someone else's export is reported as busy
    and nothing more — the folder path and row counts aren't theirs to read."""
    if _export["owner_id"] in (None, user.id) or user.role == "admin":
        return dict(_export)
    return {"state": "busy" if _export["state"] == "running" else "idle",
            "step": "Another export is running", "percent": 0,
            "result": None, "error": None, "by": _export["by"]}


@router.get("/export")
def export_status(user: User = Depends(get_current_user)):
    """Current (or last) export job. Safe to poll."""
    return {**_visible_job(user),
            "default_path": str(bundler.default_output_root()),
            "platforms": sorted(bundler.TEMPLATES),
            # Which of those this machine can actually produce a LICENSED copy for.
            # A compiled build only runs on the system it was compiled on, so the
            # screen can say so before somebody sends a customer a folder that
            # cannot start.
            "compiled_platform": bundler.host_platform(),
            "compiled_ready": bundler.compiled_available(),
            # Which platforms have binaries here NOW. One machine can hold both.
            "ready_platforms": bundler.ready_platforms(),
            "can_export_all": user.role == "admin"}


@router.get("/export/history")
def export_history(limit: int = 15, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Past exports, newest first. A normal user sees only their own; an admin sees
    everyone's, since they are the one who has to find and clear the folders."""
    limit = min(max(1, limit), 50)
    q = db.query(AuditLog).filter(AuditLog.action == "export_bundle")
    if user.role != "admin":
        q = q.filter(AuditLog.user_id == user.id)
    rows = q.order_by(AuditLog.id.desc()).limit(limit).all()

    names = {}
    if rows:
        ids = {r.user_id for r in rows if r.user_id}
        if ids:
            names = {u.id: (u.name or u.email)
                     for u in db.query(User).filter(User.id.in_(ids)).all()}

    items = []
    for r in rows:
        try:
            meta = json.loads(r.meta) if r.meta else {}
        except ValueError:
            meta = {}
        path = meta.get("zip") or meta.get("folder")
        items.append({
            "id": r.id,
            "at": r.created_at.isoformat(timespec="seconds") if r.created_at else None,
            "by": names.get(r.user_id, "—"),
            "mine": r.user_id == user.id,
            "platform": meta.get("platform"),
            "scope": meta.get("scope", "all"),
            "state": meta.get("state", "done"),
            "error": meta.get("error"),
            "with_data": bool(meta.get("with_data")),
            "path": path,
            "name": os.path.basename(path) if path else None,
            # Folders get deleted after they've been copied to a USB drive; saying
            # so beats listing a path that leads nowhere.
            "exists": bool(path and os.path.exists(path)),
            "bytes": meta.get("bytes"),
            "media_files": meta.get("media_files"),
        })
    return {"items": items}


@router.post("/export")
def export_start(request: Request, body: dict = Body(default={}),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start building a portable copy of the app for another computer.

    scope 'mine' (the default) copies only the caller's own data and re-encrypts
    their vault under a new key. scope 'all' copies the entire system and is
    admin-only — it would otherwise hand any user everyone else's records.
    """
    rate_limit(request, "export-bundle", limit=6, window=600)
    platform = str(body.get("platform") or "").strip().lower()
    if platform not in bundler.TEMPLATES:
        raise HTTPException(422, "Choose either Windows or Mac")
    scope = str(body.get("scope") or "mine").strip().lower()
    if scope not in ("mine", "all", "licence"):
        raise HTTPException(422, "scope must be 'mine', 'all' or 'licence'")
    if scope == "all" and user.role != "admin":
        raise HTTPException(403, "Only an administrator can export everyone's data")

    # A licensed build: an empty app for a customer, carrying their signed licence.
    # Never their data or anyone else's — this is software being handed over, not
    # records. include_data stays on so the copy arrives with a working database
    # holding just their sign-in.
    licence_token = licence_tag = ""
    hosting: dict | None = None
    if scope == "licence":
        if user.role != "admin":
            raise HTTPException(403, "Only an administrator can build a licensed copy")
        lic = db.query(License).filter(License.id == body.get("licence_id")).first()
        if not lic:
            raise HTTPException(404, "Licence not found")
        if lic.revoked_at:
            raise HTTPException(409, "That licence has been withdrawn — restore it first")
        if not lic.token:
            raise HTTPException(409, "That licence has no signed token to ship")
        licence_token, licence_tag = lic.token, lic.key_id
        if lic.hostname and lic.tunnel_token:
            hosting = {"hostname": lic.hostname, "tunnel_token": lic.tunnel_token}
        lic.bundle_at = ist.now()
        db.commit()

    include_data = bool(body.get("include_data", True))
    # Defaults to ON. A client that doesn't send the field at all — an older cached
    # build, say — should still get told when its build finishes; silence is the
    # worse failure. Unticking the box sends notify:false explicitly.
    notify = bool(body.get("notify", True)) and settings.push_enabled

    with _export_lock:
        # A build that has been "running" far longer than any real one is a leftover
        # from a process killed mid-job. Without this the flag never clears and every
        # later export is refused until the server is restarted.
        started = _export.get("started_at") or 0
        stale = _export["state"] == "running" and (time.time() - started) > STALE_AFTER
        if _export["state"] == "running" and not stale:
            raise HTTPException(409, "An export is already running. Try again in a minute.")
        _export.update(state="running", step="Starting", percent=0, result=None,
                       error=None, started_at=time.time(), finished_at=None,
                       by=user.name or user.email, owner_id=user.id, scope=scope)

    threading.Thread(target=_run_export,
                     args=(platform, include_data, user.id, scope, notify,
                           licence_token, licence_tag, hosting),
                     daemon=True).start()
    return {**_visible_job(user), "notify": notify}
