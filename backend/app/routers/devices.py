"""Sending photos in from a phone, without going through the phone's browser.

WHY THIS EXISTS
A web page cannot read an iPhone's photo library. The file picker is the only way
in, and above roughly a hundred photos iOS stops closing it — measured on a real
device, in a Safari tab and in the Home-Screen copy alike, with local photos and
no format conversion involved. No web API raises that ceiling and none can cap
what the picker offers, so the whole-library backup people actually want cannot
be built out of a file input however it is dressed up.

The Shortcuts app has the access the browser is denied. A shortcut can take every
photo in the library and post them here one at a time, on a schedule, overnight,
with no picker at all.

WHAT THAT COSTS, AND WHY IT IS SHAPED LIKE THIS
A shortcut has to carry a credential, and a credential in an automation is one
the owner may export, share or forget. So it is emphatically not a session token:

  * It reaches ONE endpoint, the one below, which accepts nothing but an image.
    That is true because no other route will look at one — not because a scope
    field is checked correctly everywhere, which is the kind of thing that is
    true until the day somebody adds a route and forgets.
  * It cannot read. There is no device-token route that returns a photo, a record
    or anything else, so a leaked token cannot be turned into a copy of the
    library it was granted to fill.
  * It is stored hashed and shown once, so this database is not a place where
    working credentials sit.
  * It is revocable, and revoking keeps the row — "that phone stopped working on
    Tuesday" is worth being able to answer.

The per-module permission check is still applied on every upload, against the
user the token belongs to. A token must not outlive the permission that justified
it: take gallery access away and the shortcut stops working, without anyone
having to remember it exists.
"""
import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import indexer, ist, shortcutfile, weburl
from ..database import get_db
from ..helpers import audit
from ..models import DeviceToken, User, UserModule
from ..ratelimit import rate_limit
from ..security import get_current_user
from .gallery import MAX_BYTES, store_photo

router = APIRouter(prefix="/api/devices", tags=["devices"])

# Long enough that guessing is not a strategy. urlsafe so it survives being typed
# into the Shortcuts app by hand, which is how it will usually get there.
TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row(t: DeviceToken) -> dict:
    return {
        "id": t.id,
        "name": t.name or "",
        "prefix": t.prefix or "",
        "uploads": int(t.uploads or 0),
        "created_at": ist.fmt(t.created_at),
        "last_used_at": ist.fmt(t.last_used_at) if t.last_used_at else None,
        "revoked": bool(t.revoked_at),
    }


@router.get("")
def listing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (db.query(DeviceToken)
            .filter(DeviceToken.user_id == user.id)
            .order_by(DeviceToken.id.desc()).all())
    return {"devices": [_row(t) for t in rows]}


@router.post("")
def issue(request: Request, body: dict | None = None,
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Mint a token for one phone. The secret is in this response and nowhere else."""
    name = ((body or {}).get("name") or "My phone").strip()[:60] or "My phone"
    secret = secrets.token_urlsafe(TOKEN_BYTES)
    now = ist.now()
    row = DeviceToken(user_id=user.id, name=name, token_hash=_hash(secret),
                      prefix=secret[:8], uploads=0, created_at=now)
    db.add(row)
    db.commit()
    db.refresh(row)
    # The secret is deliberately NOT in the audit note. An audit trail somebody can
    # read a live credential out of is a second place it leaks from.
    audit(db, user.id, "device_token_issue", "device", row.id,
          {"label": name}, request=request)

    # Build the shortcut here, while the secret still exists in memory. There is
    # no later moment when this is possible: the token is stored hashed, so a
    # "make me the shortcut" endpoint called tomorrow would have nothing to put
    # in it.
    base = (weburl.public_url(db) or "").rstrip("/")
    ready = None
    if base:
        try:
            from .branding import app_name
            _sweep()
            nonce = secrets.token_urlsafe(24)
            _PENDING[nonce] = (time.time() + SHORTCUT_TTL,
                               shortcutfile.build(f"{base}/api/devices/upload",
                                                  secret, app_name(db)))
            # .shortcut on the end is load-bearing — see shortcut_file().
            ready = f"{base}/api/devices/shortcut/{nonce}.shortcut"
        except Exception as exc:
            # A shortcut that could not be built must not cost them the token —
            # the manual steps work with it either way.
            print(f"[devices] could not build the shortcut: {exc}")
    return {**_row(row), "token": secret, "shortcut_url": ready,
            "upload_url": f"{base}/api/devices/upload" if base else ""}


# A built shortcut, waiting to be collected. In memory, one use, minutes long.
#
# It has to be fetched by the Shortcuts app, which carries no sign-in of ours, so
# the link cannot be behind the session — and the file contains the token in
# plain text, because that is what the shortcut needs to work. So: an unguessable
# name, one collection only, and a short life. It is the same secret going to the
# same person's phone that the screen already showed them; this is a delivery
# mechanism, not a second place it lives.
_PENDING: dict[str, tuple[float, bytes]] = {}
SHORTCUT_TTL = 900          # seconds


def _sweep() -> None:
    now = time.time()
    for k in [k for k, (exp, _) in _PENDING.items() if exp < now]:
        _PENDING.pop(k, None)


@router.get("/shortcut/{nonce}")
def shortcut_file(nonce: str):
    """Hand the built shortcut to the Shortcuts app.

    THE NAME HAS TO END IN .shortcut. iOS decides what a link is from its
    extension before it has fetched anything, and answered a URL without one with
    "the shortcut URL provided was invalid" — a message about the URL, which sent
    me looking at the address rather than at its last eight characters.

    READABLE MORE THAN ONCE, within its fifteen minutes. It was single-use
    originally, on the reasoning that a collected credential is a spent one. But
    iOS fetches this more than once — a look before the import, then the import —
    so the second read got the 404 meant for a stranger, and the honest reading of
    that failure was "this link is broken". The protection that matters is the
    unguessable name and the short life, neither of which this weakens.
    """
    _sweep()
    found = _PENDING.get(nonce.removesuffix(".shortcut"))
    if not found:
        raise HTTPException(404, "This link has expired — make a new token")
    # octet-stream, not x-plist: given a type it thinks it can render, Safari
    # shows the file instead of handing it to Shortcuts.
    return Response(content=found[1],
                    media_type="application/octet-stream",
                    headers={"Content-Disposition":
                             'attachment; filename="Back up my photos.shortcut"',
                             "Cache-Control": "no-store"})


@router.delete("/{tid}")
def revoke(tid: int, request: Request,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = (db.query(DeviceToken)
           .filter(DeviceToken.id == tid, DeviceToken.user_id == user.id).first())
    if not row:
        raise HTTPException(404, "No such device")
    if not row.revoked_at:
        row.revoked_at = ist.now()
        db.commit()
    audit(db, user.id, "device_token_revoke", "device", row.id,
          {"label": row.name or ""}, request=request)
    return _row(row)


def _bearer(authorization: str | None, x_device_token: str | None) -> str:
    """Accept the token either way round.

    Shortcuts can set any header, but "Authorization: Bearer …" is the one people
    already have muscle memory for, and a wrong guess here is a failure with
    nothing on screen to explain it.
    """
    if x_device_token:
        return x_device_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


@router.post("/upload")
def device_upload(request: Request, file: UploadFile = File(...),
                  authorization: str | None = Header(default=None),
                  x_device_token: str | None = Header(default=None),
                  db: Session = Depends(get_db)):
    """The only thing a device token can do.

    Synchronous on purpose, exactly as /gallery/upload is: the work below is
    blocking image processing, and on the event loop it would serialise every
    upload and stall the rest of the app while a phone empties its library into it.
    """
    # A bulk backup is thousands of photos, so this is generous — it is here to
    # stop a runaway loop, not to pace a legitimate one.
    rate_limit(request, "device-upload", limit=5000, window=3600)

    presented = _bearer(authorization, x_device_token)
    if not presented:
        raise HTTPException(401, "Add your device token to this request")

    row = (db.query(DeviceToken)
           .filter(DeviceToken.token_hash == _hash(presented)).first())
    if not row or row.revoked_at:
        # One message for both, so a caller cannot tell an unknown token from a
        # withdrawn one and go looking for the difference.
        raise HTTPException(401, "This device token is not valid")

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user or (user.status or "active") != "active":
        raise HTTPException(401, "This device token is not valid")

    # Checked on every upload rather than trusted from issue time: a token must not
    # outlive the permission that justified it. Admins bypass, as they do everywhere.
    if user.role != "admin":
        perm = (db.query(UserModule)
                .filter(UserModule.user_id == user.id,
                        UserModule.module_key == "gallery").first())
        if not perm or not perm.can_create:
            raise HTTPException(403, "This account can no longer add photos")

    chunks, total = [], 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise HTTPException(413, f"Photo too large (max {MAX_BYTES // (1024 * 1024)} MB)")
        chunks.append(chunk)

    # A shortcut emptying a phone is a bulk upload like any other, so the indexer
    # and the folder watcher stand aside for it too.
    indexer.note_upload()
    out = store_photo(db, user, b"".join(chunks), file.filename or "photo.jpg")

    row.uploads = int(row.uploads or 0) + 1
    row.last_used_at = ist.now()
    db.commit()
    # Deliberately no audit row per photo. A backup is thousands of them, and an
    # activity log nobody can read past is one that has stopped working.
    return {"ok": True, "duplicate": bool(out.get("duplicate"))}
