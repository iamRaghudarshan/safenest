"""The publisher's customer-facing storefront: download the app, request a licence.

Registered ONLY on the publisher installation (main.py guards on
settings.is_publisher), so it never exists in a customer copy — defence in depth
on top of the admin+publisher gates on the approval endpoints. It moves software
and licences, never anyone's records, so it does not weaken the promise that
customer data stays on the customer's machine.

Three audiences:
  /api/public/...          anonymous visitor — download the generic build, ask for a key
  /api/licence-requests    admin + publisher — review and approve/reject those asks
  /get                     the storefront HTML page itself
"""
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.orm import Session

from .. import bundler, ist, licensing, mailer, weburl
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import LicenceRequest, SiteStat, User
from ..ratelimit import rate_limit
from ..security import require_admin
from .branding import app_name
from .licences import _days, _require_publisher, _row, _seats, issue_and_store
from .releases import _current_for

public = APIRouter(prefix="/api/public", tags=["storefront"])
admin_r = APIRouter(prefix="/api/licence-requests", tags=["licence-requests"])
pages = APIRouter()

# backend/storefront/index.html — the page, kept out of frontend/dist so it never
# compiles into the SPA that ships to customers.
_PAGE = Path(__file__).resolve().parents[2] / "storefront" / "index.html"


def _rel_meta(rel) -> dict:
    if rel and rel.path and Path(rel.path).is_file():
        return {"available": True, "version": rel.version,
                "size_bytes": rel.size_bytes or 0, "sha256": rel.sha256,
                "notes": rel.notes or ""}
    return {"available": False}


@public.get("/download")
def public_download(request: Request, platform: str = "", db: Session = Depends(get_db)):
    """Serve the current GENERIC build to an anonymous visitor.

    No licence key involved: the download is the software, and the customer
    activates it afterwards with the key they were issued. Reuses the same
    'current release for this platform' logic the update path uses, minus the
    per-licence check that guards the customer update download.
    """
    rate_limit(request, "public-download", limit=30, window=3600)
    rel = _current_for(platform, db)
    if not rel or not (rel.path and Path(rel.path).is_file()):
        raise HTTPException(404, "No download is available yet.")

    path, filename = Path(rel.path), rel.filename
    # Prefer the installer variant if one exists. The release file itself is what the
    # auto-update swap consumes — a bare Mac .app (which macOS quarantines) or a bare
    # Windows App\App.exe (unbranded, no instructions). The installer sibling is the
    # same app renamed to the brand with a one-click setup / README, meant for a
    # person to download and open. See releases.publish + bundler.build_*_release.
    for ext in (".tar.gz", ".zip"):
        if filename.endswith(ext):
            cand = path.with_name(path.name[:-len(ext)] + "-installer" + ext)
            if cand.is_file():
                path, filename = cand, cand.name
            break
    # A Mac release is a tar.gz — a zip cannot carry the Python.framework symlinks.
    kind = ("application/gzip" if filename.endswith(".tar.gz") else "application/zip")
    return FileResponse(str(path), filename=filename, media_type=kind)


@public.get("/download/meta")
def public_download_meta(request: Request, db: Session = Depends(get_db)):
    """Version, size and checksum per platform, for the download page to render."""
    rate_limit(request, "public-download-meta", limit=120, window=3600)
    return {"app_name": app_name(db),
            "platforms": {p: _rel_meta(_current_for(p, db)) for p in bundler.TEMPLATES}}


@public.post("/licence-request")
def request_licence(request: Request, body: dict = Body(...),
                    db: Session = Depends(get_db)):
    """A visitor asks for a licence. Stored for the publisher to approve by hand.

    Never confirms whether an email already has a licence — that would let anyone
    enumerate the customer list. The answer is the same either way.
    """
    rate_limit(request, "licence-request", limit=5, window=3600)
    name = (body.get("name") or "").strip()[:120]
    email = (body.get("email") or "").strip().lower()[:160]
    if not name:
        raise HTTPException(422, "Please enter your name.")
    if not licensing.looks_like_email(email):
        raise HTTPException(422, "Please enter a valid email address.")

    # One open request per email: a second press, or a spammer, must not pile up
    # duplicate rows for the same person. Idempotent to the visitor either way.
    existing = (db.query(LicenceRequest)
                .filter(LicenceRequest.email == email,
                        LicenceRequest.status == "pending").first())
    if not existing:
        message = (body.get("message") or "").strip()[:500]
        platform = (body.get("platform") or "").strip().lower()[:16]
        db.add(LicenceRequest(
            name=name, email=email, message=message, platform=platform,
            status="pending",
            source_ip=(request.client.host if request.client else "")[:45],
            created_at=ist.now()))
        db.commit()
        # Tell the publisher a new request came in, to the configured 'from' inbox.
        m = mailer.settings_row(db)
        if mailer.is_configured(db) and m and m.from_addr:
            mailer.enqueue(db, m.from_addr, f"New licence request - {name}",
                f"{name} <{email}> requested a licence"
                + (f" (platform: {platform})" if platform else "") + ".\n"
                + (f"\nMessage:\n{message}\n" if message else "")
                + "\nReview and approve it in the app under Licences.", kind="request")
    return {"ok": True, "queued": True}


def _request_row(r: LicenceRequest) -> dict:
    return {"id": r.id, "name": r.name, "email": r.email, "message": r.message,
            "platform": r.platform, "status": r.status, "key_id": r.key_id,
            "reject_reason": r.reject_reason,
            "created_at": ist.fmt(r.created_at), "handled_at": ist.fmt(r.handled_at)}


@admin_r.get("")
def list_requests(status: str = "", admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    _require_publisher()
    q = db.query(LicenceRequest).order_by(LicenceRequest.id.desc())
    if status:
        q = q.filter(LicenceRequest.status == status[:16])
    rows = q.all()
    return {"requests": [_request_row(r) for r in rows],
            "pending": sum(1 for r in rows if r.status == "pending")}


@admin_r.post("/{req_id}/approve")
def approve_request(req_id: int, request: Request, body: dict = Body(default={}),
                    admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Issue a licence for a pending request. Returns the signed token to hand over.

    Reuses the same signing path as the admin create() screen (issue_and_store),
    so a storefront-issued licence is identical to a hand-issued one.
    """
    _require_publisher()
    req = db.query(LicenceRequest).filter(LicenceRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != "pending":
        raise HTTPException(409, f"This request is already {req.status}.")

    days = _days(body)          # honours perpetual, and the falsy-zero care
    seats = _seats(body)
    note = (body.get("note") or f"Storefront request #{req.id}").strip()
    lic, token = issue_and_store(db, name=req.name, email=req.email, days=days,
                                 seats=seats, note=note, created_by=admin.id,
                                 allow_duplicate=bool(body.get("allow_duplicate")))
    req.status = "approved"
    req.key_id = lic.key_id
    req.handled_at = ist.now()
    req.handled_by = admin.id
    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_issue", "licence", lic.id,
          {"label": f"{req.name} ({lic.key_id})", "email": req.email,
           "via": "storefront", "days": days, "seats": seats}, request=request)

    # Email the customer their key + download link, if SMTP is configured. Never
    # let a mail failure undo an already-issued licence.
    emailed = False
    if mailer.is_configured(db):
        base = weburl.public_url(db).rstrip("/")
        mailer.enqueue(db, req.email, f"Your {app_name(db)} licence",
            f"Hi {req.name},\n\nYour licence is ready.\n\n"
            f"Licence key:\n{token}\n\n"
            f"1. Download the app: {base}/get\n"
            f"2. Open it, choose Activate, and paste the key above.\n\n"
            f"Thank you.", kind="licence")
        emailed = True
    return {**_row(lic), "token": token, "emailed": emailed}


@admin_r.post("/{req_id}/reject")
def reject_request(req_id: int, body: dict = Body(default={}),
                   admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    req = db.query(LicenceRequest).filter(LicenceRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != "pending":
        raise HTTPException(409, f"This request is already {req.status}.")
    req.status = "rejected"
    req.reject_reason = (body.get("reason") or "").strip()[:200]
    req.handled_at = ist.now()
    req.handled_by = admin.id
    db.commit()
    return {"ok": True}


def _track_visit(db: Session) -> None:
    """Count one open of the public site, per day. Best-effort — a tracking error
    must never stop the page loading."""
    try:
        day = ist.today().isoformat()
        row = db.query(SiteStat).filter(SiteStat.day == day).first()
        if row:
            row.visits = int(row.visits or 0) + 1
        else:
            db.add(SiteStat(day=day, visits=1))
        db.commit()
    except Exception:
        db.rollback()


@pages.get("/get", include_in_schema=False)
def storefront_page(db: Session = Depends(get_db)):
    """The download / request-a-licence landing page."""
    if _PAGE.is_file():
        _track_visit(db)
        return HTMLResponse(_PAGE.read_text(encoding="utf-8"))
    raise HTTPException(404, "The storefront page is not available.")


# Served as a separate file, not inline in the page, because the app's CSP is
# script-src 'self' — inline <script> is blocked, a same-origin file is allowed.
_SCRIPT = _PAGE.parent / "storefront.js"


@pages.get("/storefront.js", include_in_schema=False)
def storefront_script():
    if _SCRIPT.is_file():
        return Response(_SCRIPT.read_text(encoding="utf-8"),
                        media_type="application/javascript")
    raise HTTPException(404, "Not found")


@pages.get("/install-mac.sh", include_in_schema=False)
def install_mac_script(db: Session = Depends(get_db)):
    """A one-line Terminal installer for macOS — the reliable way to install an app
    Apple has not notarised.

    THE PROBLEM. macOS quarantines anything a BROWSER downloads. An app that is not
    signed with an Apple 'Developer ID' certificate AND notarised then reads as
    'damaged' (Apple silicon) or 'unidentified developer', and recent macOS removed
    the right-click -> Open bypass. Even the installer .command is itself blocked,
    because it too was quarantined by the browser.

    THE FIX WITHOUT AN APPLE ACCOUNT. Files fetched with `curl` are NOT quarantined
    (only apps that opt into LSFileQuarantine, i.e. browsers, set the flag). So the
    customer pastes ONE line; curl fetches this script (unquarantined), which curls
    the app (unquarantined), strips any stray attribute, ad-hoc re-signs, moves it to
    Applications and opens it. No Gatekeeper block, no Terminal spelunking.

    The proper fix is notarisation (see .github/workflows/build-mac.yml) — then a
    plain double-click works and this is unnecessary.
    """
    brand = bundler._display_name(app_name(db))
    base = (weburl.public_url(db) or settings.public_base_url or "").rstrip("/")
    dl = f"{base}/api/public/download?platform=mac"
    script = f"""#!/bin/bash
# {brand} installer for macOS. Run via curl so the app is never quarantined.
set -e
BRAND="{brand}"
URL="{dl}"
# Supported on Apple Silicon (arm64), macOS 11+. Fail CLEARLY on anything else
# rather than installing an app the Mac cannot launch and leaving a cryptic crash.
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  echo ""
  echo "  $BRAND runs on Apple Silicon Macs (M1, M2, M3 or newer)."
  echo "  This Mac reports $ARCH, which looks like an older Intel Mac and is not supported."
  echo ""
  exit 1
fi
OSMAJ="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
if [ -z "$OSMAJ" ]; then OSMAJ=0; fi
if [ "$OSMAJ" -lt 11 ]; then
  echo ""
  echo "  $BRAND needs macOS 11 (Big Sur) or newer. Please update macOS and try again."
  echo ""
  exit 1
fi
echo ""
echo "  Installing $BRAND for macOS..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "  Downloading..."
curl -fL# "$URL" -o "$TMP/app.tar.gz"
echo "  Unpacking..."
tar -xzf "$TMP/app.tar.gz" -C "$TMP"
APP="$(/usr/bin/find "$TMP" -maxdepth 3 -name "$BRAND.app" -type d | head -1)"
if [ -z "$APP" ]; then echo "  Could not find $BRAND.app in the download."; exit 1; fi
echo "  Setting up..."
xattr -cr "$APP" 2>/dev/null || true
codesign --force --deep -s - "$APP" 2>/dev/null || true
DEST="/Applications/$BRAND.app"
rm -rf "$DEST" 2>/dev/null || true
if ! cp -R "$APP" /Applications/ 2>/dev/null; then
  # No permission for /Applications (a standard, non-admin Mac account). Install
  # into the user's OWN Applications folder instead. NEVER run from $TMP: the trap
  # above deletes it the instant this script exits, which yanks the app out from
  # under itself and it simply "will not open".
  mkdir -p "$HOME/Applications"
  DEST="$HOME/Applications/$BRAND.app"
  rm -rf "$DEST" 2>/dev/null || true
  if ! cp -R "$APP" "$HOME/Applications/" 2>/dev/null; then
    echo "  Could not install $BRAND. Check you have space and try again."
    exit 1
  fi
  echo "  Installed to your personal Applications folder (~/Applications)."
fi
xattr -cr "$DEST" 2>/dev/null || true
codesign --force --deep -s - "$DEST" 2>/dev/null || true
open "$DEST"
echo ""
echo "  $BRAND is installed. Open it from Applications from now on."
echo ""
"""
    return Response(script, media_type="text/x-shellscript")
