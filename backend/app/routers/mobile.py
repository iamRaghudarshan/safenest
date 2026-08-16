"""Serving the mobile app's own updates from the copy the phone already talks to.

The phone connects only to its household's own server, never a central one, so its
updates come from there too: the desktop copy hosts the current Android APK and the
phone checks for a newer version against it. iOS updates through the App Store and
never touches this.

The APK and its metadata live in a `mobile/` folder beside the app:
    mobile/android.json    {"version": "1.17.0", "notes": "...", "filename": "app-release.apk"}
    mobile/app-release.apk
A later step wires the desktop release process to drop the CI-built APK here; for
now the endpoints answer "nothing available" until a file is present, which is the
correct, safe default.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import bundler
from ..models import User
from ..ratelimit import rate_limit
from ..security import get_current_user

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


def _mobile_dir() -> Path:
    return bundler.PROJECT_ROOT / "mobile"


def _meta(platform: str) -> dict | None:
    f = _mobile_dir() / f"{platform}.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


@router.get("/latest")
def latest(request: Request, platform: str = "android",
           user: User = Depends(get_current_user)):
    """The newest mobile build on offer, for the app's own update check.

    Returns a RELATIVE download path, not an absolute URL: the phone reaches this
    server by whatever address it happens to be on (home Wi-Fi or the tunnel), so
    it resolves the path against the base it is already using rather than being
    handed one that might be the wrong side of the house.
    """
    rate_limit(request, "mobile-latest", limit=60, window=60)
    plat = (platform or "android").strip().lower()
    meta = _meta(plat)
    if not meta or not meta.get("filename"):
        return {"available": False}
    apk = _mobile_dir() / meta["filename"]
    if not apk.is_file():
        return {"available": False}
    return {"available": True, "platform": plat,
            "version": str(meta.get("version", "")),
            "notes": str(meta.get("notes", "")),
            "size_bytes": apk.stat().st_size,
            "url": f"/api/mobile/download?platform={plat}"}


@router.get("/download")
def download(request: Request, platform: str = "android",
             user: User = Depends(get_current_user)):
    """The APK file itself, for the phone to install."""
    rate_limit(request, "mobile-download", limit=12, window=600)
    plat = (platform or "android").strip().lower()
    meta = _meta(plat)
    apk = _mobile_dir() / (meta.get("filename") if meta else "")
    if not meta or not meta.get("filename") or not apk.is_file():
        raise HTTPException(404, "No mobile build is available")
    return FileResponse(apk, filename=apk.name,
                        media_type="application/vnd.android.package-archive")
