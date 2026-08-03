"""The app's own name and icon, set by an administrator from inside the app.

Three kinds of route live here, and the split is deliberate:

  GET  /api/branding      public. The login screen has to know what the app is
                          called before anyone has signed in, so this cannot
                          require a token. It returns only the name, colour and
                          icon version — nothing about users or data.
  PUT  /api/branding      admin. Changes the name.
  POST /api/branding/icon admin. Uploads a picture and renders every size the
                          browsers and home screens ask for.

  GET  /branding/icon-*.png   public files, referenced by the manifest, the
  GET  /manifest.webmanifest  favicon and the iOS home-screen icon. They sit
                              outside /api because that is where the browser
                              expects them, and they must be reachable with no
                              credentials — a home-screen icon is fetched by the
                              operating system, not by the signed-in page.
"""
import io
import os

from fastapi import (APIRouter, Body, Depends, File, HTTPException, Request, Response,
                     UploadFile)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit
from ..models import Branding, User
from ..ratelimit import rate_limit
from ..security import require_admin
from ..storage import PRIVATE_ROOT

router = APIRouter(prefix="/api/branding", tags=["branding"])
files = APIRouter(tags=["branding"])          # no /api prefix — browser-facing

def _build_brand() -> dict:
    """What this build calls itself, written into it at build time.

    WHY THE DEFAULT IS NOT A CONSTANT ANY MORE
    _row() creates a branding row the first time anything asks for the name, and
    it used the literal "App". So any copy running on a database it made for
    itself -- a records folder that never received the shipped one, an account
    created before the seed was copied out -- called itself "App" over the stock
    rupee mark, on a customer's machine, for ever. Three separate attempts to
    repair that row after the fact each missed a case, because they were all
    fixing the symptom: a build that did not know its own name.

    It knows now. packaging/build_exe.py writes brand.json beside the web files
    and stamps the icons into them, so the fallback IS the product rather than a
    placeholder. Nothing has to be restored for a copy to look right.

    A name set deliberately still wins: this is only ever the default.
    """
    import json
    from pathlib import Path
    try:
        base = os.environ.get("FRONTEND_DIST")
        root = Path(base) if base else Path(__file__).resolve().parents[3] / "frontend" / "dist"
        return json.loads((root / "brand.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_BUILD = _build_brand()
DEFAULT_NAME = (_BUILD.get("app_name") or "App").strip() or "App"
DEFAULT_SHORT = (_BUILD.get("short_name") or DEFAULT_NAME).strip()
DEFAULT_TAGLINE = (_BUILD.get("tagline") or "").strip()
DEFAULT_THEME = (_BUILD.get("theme_color") or "#5b3df5").strip()
MAX_UPLOAD = 6 * 1024 * 1024                  # 6 MB; a launcher icon is never bigger

# Every size a browser or home screen asks for. 32 is the favicon, 180 is what
# iOS uses for apple-touch-icon, 192/512 are the PWA manifest sizes.
SIZES = (32, 180, 192, 512)

BRAND_DIR = os.path.join(PRIVATE_ROOT, "branding")


def _row(db: Session) -> Branding:
    """The single branding row, created on first use.

    The insert has to tolerate losing a race. On a fresh install the first page
    load fires several unauthenticated reads at once — the app itself, the
    manifest and the favicon — and each would otherwise try to create id 1. The
    loser of that race gets an integrity error, which would surface as a 500 on
    the login screen of a brand new copy: the worst possible first impression,
    and only on first run, so easy to never see in testing.
    """
    row = db.query(Branding).filter(Branding.id == 1).first()
    if row:
        return row
    try:
        row = Branding(id=1, app_name=DEFAULT_NAME, short_name=DEFAULT_SHORT,
                       tagline=DEFAULT_TAGLINE, theme_color=DEFAULT_THEME,
                       icon_version=0, updated_at=ist.now())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        # Somebody else created it a moment ago — take theirs.
        db.rollback()
        row = db.query(Branding).filter(Branding.id == 1).first()
        if row:
            return row
        raise


def _icon_path(size: int) -> str:
    return os.path.join(BRAND_DIR, f"icon-{size}.png")


_cached_name: str | None = None


def app_name_cached() -> str:
    """The app name for hot paths -- the Server header, set on every response.

    app_name() opens a database session, which is fine for a notification and
    absurd for something that runs on every single request. Cleared by
    forget_name() whenever the branding is edited, so a rename still takes effect
    without a restart.
    """
    global _cached_name
    if _cached_name is None:
        _cached_name = app_name()
    return _cached_name


def forget_name() -> None:
    global _cached_name
    _cached_name = None


def app_name(db: Session | None = None) -> str:
    """What this installation calls itself, for any string a person will read.

    Exists because the name was hard-coded in a dozen places the branding screen
    could not reach -- push notification titles, the export-ready alert, the
    "this is not a X licence" message. Renaming the app left those still saying
    the old name, which is exactly the sort of half-rebranded copy a customer
    notices first.

    Takes an open session when the caller already has one, opens its own when
    not, and never raises: every caller is either a notification or an error
    message, and neither may fail because of the app's own name.
    """
    try:
        if db is not None:
            return current(db)["app_name"]
        from ..database import SessionLocal
        s = SessionLocal()
        try:
            return current(s)["app_name"]
        finally:
            s.close()
    except Exception:
        return DEFAULT_NAME


def current(db: Session) -> dict:
    """Shared by the API, the manifest and anything else that needs the name."""
    row = _row(db)
    v = int(row.icon_version or 0)
    return {
        "app_name": row.app_name or DEFAULT_NAME,
        "short_name": row.short_name or row.app_name or DEFAULT_NAME,
        "tagline": row.tagline or "",
        "theme_color": row.theme_color or DEFAULT_THEME,
        "icon_version": v,
        # When no icon has been uploaded these point at the files shipped with the
        # build, so the app always has a working icon rather than a broken image.
        "icons": {
            str(s): (f"/branding/icon-{s}.png?v={v}" if v else _shipped(s))
            for s in SIZES
        },
    }


def _shipped(size: int) -> str:
    """The icon that came with the build, for a copy that never uploaded one."""
    if size == 180:
        return "/apple-touch-icon.png"
    if size == 32:
        return "/icon-192.png"          # no 32px file ships; the browser scales it
    return f"/icon-{size}.png"


# ------------------------------------------------------------------ public read
@router.get("")
def read(request: Request, db: Session = Depends(get_db)):
    """What this app is called. Unauthenticated on purpose — see the module note.

    Rate limited like every other public endpoint. The ceiling is deliberately
    high: a normal page load calls this once, so it only ever catches abuse.
    """
    rate_limit(request, "branding", limit=120, window=60)
    return current(db)


# ----------------------------------------------------------------- admin writes
@router.put("")
def update(body: dict = Body(...), admin: User = Depends(require_admin),
           db: Session = Depends(get_db)):
    row = _row(db)
    name = (body.get("app_name") or "").strip()[:60]
    if not name:
        raise HTTPException(422, "The app needs a name")
    short = (body.get("short_name") or "").strip()[:20] or name[:20]
    theme = (body.get("theme_color") or "").strip()[:20] or DEFAULT_THEME
    if not (theme.startswith("#") and len(theme) in (4, 7)):
        raise HTTPException(422, "Theme colour must be a hex value like #1656C6")

    row.app_name = name
    row.short_name = short
    row.tagline = (body.get("tagline") or "").strip()[:120]
    row.theme_color = theme
    row.updated_at = ist.now()
    row.updated_by = admin.id
    db.commit()
    forget_name()   # the Server header caches it; a rename must not need a restart
    audit(db, admin.id, "branding_update", "branding", 1, {"label": name})
    return current(db)


@router.post("/icon")
async def upload_icon(file: UploadFile = File(...), admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """Take one picture and render every size the platforms ask for.

    Rendering here rather than trusting the upload matters: a home screen given a
    3000px photo would either be rejected or scaled badly by each platform in its
    own way, and an icon that looks right on Android and wrong on iOS is the
    usual result of shipping a single file.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:                       # pragma: no cover - Pillow always ships
        raise HTTPException(500, "Image support is not installed on this server")

    raw, total = [], 0
    while True:
        chunk = await file.read(1024 * 256)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD:
            raise HTTPException(413, "That image is too large (6 MB maximum)")
        raw.append(chunk)
    data = b"".join(raw)
    if not data:
        raise HTTPException(422, "That file was empty")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise HTTPException(422, "That file is not an image we can read. Try a PNG or JPG.")

    # Flatten onto a transparent canvas and square it off by padding rather than
    # cropping — cropping a wide logo silently cuts its ends off, which the person
    # uploading it would only discover on their phone's home screen.
    img = ImageOps.exif_transpose(img).convert("RGBA")
    side = max(img.width, img.height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))

    os.makedirs(BRAND_DIR, exist_ok=True)
    for size in SIZES:
        square.resize((size, size), Image.LANCZOS).save(_icon_path(size), "PNG", optimize=True)

    row = _row(db)
    row.icon_version = int(row.icon_version or 0) + 1
    row.updated_at = ist.now()
    row.updated_by = admin.id
    db.commit()
    audit(db, admin.id, "branding_icon", "branding", 1,
          {"label": f"{row.app_name} icon v{row.icon_version}"})
    return current(db)


@router.delete("/icon")
def clear_icon(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Go back to the icon that came with the build."""
    for size in SIZES:
        try:
            os.remove(_icon_path(size))
        except FileNotFoundError:
            pass
    row = _row(db)
    row.icon_version = 0
    row.updated_at = ist.now()
    row.updated_by = admin.id
    db.commit()
    audit(db, admin.id, "branding_icon_clear", "branding", 1, {"label": row.app_name})
    return current(db)


# --------------------------------------------------------------- browser-facing
@files.get("/branding/icon-{size}.png")
def icon(size: int, db: Session = Depends(get_db)):
    """Serve an uploaded icon. Falls through to the shipped one if none exists."""
    if size not in SIZES:
        raise HTTPException(404, "No icon at that size")
    path = _icon_path(size)
    if not os.path.isfile(path):
        raise HTTPException(404, "No icon uploaded")
    # The URL carries ?v=<version>, so a long cache is safe and a change is picked
    # up immediately by everything that reads the manifest.
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=604800"})


@files.get("/manifest.webmanifest")
def manifest(db: Session = Depends(get_db)):
    """The PWA manifest, built from the branding row.

    This shadows the static file of the same name in the built frontend, which is
    why it is registered before the SPA mount. Without it, installing the app to a
    home screen would keep showing whatever name was compiled in.
    """
    b = current(db)
    return JSONResponse({
        "name": b["app_name"],
        "short_name": b["short_name"],
        "description": b["tagline"] or "Everything you own, in one private place.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0d0e16",
        "theme_color": b["theme_color"],
        "icons": [
            {"src": b["icons"]["192"], "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": b["icons"]["512"], "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }, media_type="application/manifest+json",
       headers={"Cache-Control": "no-cache, must-revalidate"})


@files.get("/favicon.ico")
def favicon(db: Session = Depends(get_db)):
    """Browsers ask for this by name whatever the page's <link> says."""
    path = _icon_path(32)
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)
