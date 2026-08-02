"""The people a licensed copy is allowed to sign in — the owner and their family.

WHY THIS IS NOT PART OF admin.py
Creating a user has always been `require_admin`, and a licensed customer is given
the `user` role deliberately: they administer nothing of the publisher's, so a
customer's copy has no administrator at all. That is the right arrangement, and it
left a licence holder unable to add their own spouse or child to their own copy —
the feature existed and was permanently unreachable for exactly the people it was
for. Same shape as the web address in hosting.py, and the same answer: allowed in a
licensed copy, admin-only on the publisher's own installation.

HOW MANY
From the licence, never from a setting. `seats` is inside the signed token, so a
customer cannot raise their own limit by editing a file — changing it invalidates
the signature. A licence issued before seats existed has no such field and reads
as 1, which is what those copies could already do.
"""
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist, licensing
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import User, UserModule
from ..security import check_password_strength, get_current_user, hash_password

router = APIRouter(prefix="/api/household", tags=["household"])
updater = APIRouter(prefix="/api/update", tags=["update"])

ALL_MODULES = ["loans", "cards", "insurance", "investments", "expenses",
               "reminders", "todo", "vault", "gallery", "documents"]


def can_manage(user: User, db: Session) -> bool:
    """Who may add or remove sign-ins.

    An administrator always. In a LICENSED copy, only the licence holder -- not
    everyone signed in to it.

    This used to be any user in a licensed copy, which handed the household's
    membership to every member of it: a child added to the family could add more
    people up to the seat limit, or remove a sibling. The licence is bought by one
    person and the seats are theirs to allocate.
    """
    if user.role == "admin":
        return True
    if not settings.licensed_mode:
        return False
    return user.id == _owner_id(db)


def _owner_id(db: Session) -> int | None:
    """The account the licence was issued to.

    Matched on the licence's own email, which is what the first run creates the
    account from. If nothing matches -- the holder changed their address, say --
    the earliest account is the owner, because on a licensed copy that is the one
    the licence created.
    """
    email = (_licence().get("email") or "").strip().lower()
    if email:
        row = db.query(User).filter(func.lower(User.email) == email).first()
        if row:
            return row.id
    first = db.query(User).order_by(User.id).first()
    return first.id if first else None


def _manager(user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> User:
    if not can_manage(user, db):
        raise HTTPException(
            403, "Only the person this copy is licensed to can add or remove "
                 "sign-ins.")
    return user


def _licence() -> dict:
    """This copy's licence payload, or {} on the publisher's own installation."""
    if not settings.licensed_mode:
        return {}
    return licensing.parse(_token(), settings.license_public_key_hex)


def _token() -> str:
    try:
        with open(settings.license_path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def limits(db: Session) -> dict:
    """Seats allowed, seats used, and whether another one may be added."""
    used = db.query(User).count()
    if not settings.licensed_mode:
        # The publisher's own installation is not seat-limited; it is not licensed
        # to itself.
        return {"allowed": licensing.UNLIMITED_SEATS, "used": used,
                "unlimited": True, "can_add": True}
    allowed = licensing.seats_allowed(_licence())
    unlimited = allowed == licensing.UNLIMITED_SEATS
    return {"allowed": allowed, "used": used, "unlimited": unlimited,
            "can_add": unlimited or used < allowed}


@router.get("")
def read(user: User = Depends(_manager), db: Session = Depends(get_db)):
    """Everyone who can sign in to this copy, and how many more are permitted."""
    people = db.query(User).order_by(User.id).all()
    return {
        "people": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role,
                    "status": u.status, "initials": (u.name or u.email or "?")[:1].upper(),
                    "is_you": u.id == user.id,
                    "created_at": ist.fmt(u.created_at)} for u in people],
        **limits(db),
        "can_manage": True,
        "owner_id": _owner_id(db),
    }


@router.post("")
def add(request: Request, body: dict = Body(...), user: User = Depends(_manager),
        db: Session = Depends(get_db)):
    """Add a family member. Their records are their own — see the note below."""
    state = limits(db)
    if not state["can_add"]:
        raise HTTPException(
            409, f"This licence covers {state['allowed']} "
                 f"sign-in{'' if state['allowed'] == 1 else 's'}, and "
                 f"{state['used']} {'is' if state['used'] == 1 else 'are'} in use. "
                 "Ask your supplier to raise it.")

    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    pw = body.get("password") or ""
    if not name:
        raise HTTPException(422, "Name is required")
    if not licensing.looks_like_email(email):
        raise HTTPException(422, "A valid email address is required")
    check_password_strength(pw)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Someone already signs in with that email address")

    now = ist.now()
    # Always a plain user, never an admin, whoever is adding them. In a licensed
    # copy nobody is an administrator by design, and a household member must not
    # become the exception that quietly reintroduces one.
    person = User(name=name, email=email, password_hash=hash_password(pw),
                  role="user", status="active", created_at=now, updated_at=now)
    db.add(person)
    db.commit()
    db.refresh(person)
    # Their own modules, so what they add is theirs. Scope everywhere else in the
    # app is per user, so a family member does not see the owner's records.
    for m in ALL_MODULES:
        db.add(UserModule(user_id=person.id, module_key=m,
                          can_view=1, can_create=1, can_edit=1, can_delete=1))
    db.commit()
    audit(db, user.id, "household_add", "user", person.id,
          {"label": person.name or person.email, "email": person.email},
          request=request)
    return {"id": person.id, **limits(db)}


@router.delete("/{person_id}")
def remove(person_id: int, request: Request, user: User = Depends(_manager),
           db: Session = Depends(get_db)):
    """Remove a sign-in, freeing its seat.

    Refuses to remove the last one, and refuses to remove you. A copy with no
    accounts cannot be signed into at all, and there is no administrator anywhere
    in a licensed copy to rescue it.
    """
    person = db.query(User).get(person_id)
    if not person:
        raise HTTPException(404, "That person is not on this copy")
    if person.id == user.id:
        raise HTTPException(409, "You cannot remove your own sign-in")
    if db.query(User).count() <= 1:
        raise HTTPException(409, "This is the only sign-in on this copy")

    label = person.name or person.email
    db.query(UserModule).filter(UserModule.user_id == person.id).delete()
    db.delete(person)
    db.commit()
    audit(db, user.id, "household_remove", "user", person_id,
          {"label": label}, request=request)
    return {"removed": person_id, **limits(db)}


# --------------------------------------------------------- installing updates
#
# Lives here rather than in releases.py because this is the CUSTOMER half: their
# copy asking their supplier what is available, checking the signature itself, and
# replacing its own program files. releases.py is the publisher half and a
# customer's copy never runs it.
#
# Nothing is downloaded until somebody presses the button. An app that replaces
# itself unasked, on a machine holding someone's financial records, is not a
# convenience — and a customer who was never told would be right to be alarmed.
@updater.get("")
def check_for_update(user: User = Depends(_manager)):
    """Is a newer version being offered? Downloads nothing."""
    from .. import updates
    running = updates.current_version()
    out = {"running": running, "available": False, "installable":
           updates.install_root() is not None}
    if not settings.licensed_mode:
        out["reason"] = "This installation updates from its own source."
        return out

    payload = _licence()
    issuer = (payload.get("issuer") or settings.license_check_url or "").rstrip("/")
    kid = payload.get("kid")
    if not (issuer and kid):
        out["reason"] = "This copy does not know where to check."
        return out
    try:
        import requests
        r = requests.get(f"{issuer}/api/licence/update/{kid}", timeout=15)
        data = r.json()
    except Exception as exc:
        # Offline is not an error worth a red screen; it is Tuesday.
        out["reason"] = f"Could not reach your supplier ({exc.__class__.__name__})."
        return out
    if not data.get("available"):
        out["reason"] = "You are on the newest version."
        return out
    try:
        manifest = updates.verify(data.get("manifest", ""),
                                  settings.license_public_key_hex)
    except updates.UpdateError as exc:
        out["reason"] = str(exc)
        return out
    if not updates.is_newer(manifest["version"], running):
        out["reason"] = "You are on the newest version."
        return out
    out.update({"available": True, "version": manifest["version"],
                "notes": manifest.get("notes", ""),
                "size_mb": round(manifest.get("size", 0) / 1048576, 1)})
    return out


@updater.post("")
def install_update(request: Request, user: User = Depends(_manager),
                   db: Session = Depends(get_db)):
    """Download the offered version, check it, and restart into it.

    Every step can refuse. The signature is checked before the file is fetched,
    the checksum after, and the archive's paths before anything is written — an
    update that fails any of them is discarded and this copy carries on unchanged.

    Records are never part of it: only the executable and _internal are replaced,
    the staged copy is unpacked outside the app folder, and the swap script
    excludes `data`. Any schema change is applied on the next launch, which backs
    the database up before it touches it.
    """
    from .. import updates
    if updates.install_root() is None:
        raise HTTPException(409, "Updates only apply to an installed copy.")
    payload = _licence()
    issuer = (payload.get("issuer") or settings.license_check_url or "").rstrip("/")
    kid = payload.get("kid")
    if not (issuer and kid):
        raise HTTPException(409, "This copy does not know where to check.")

    import requests
    try:
        info = requests.get(f"{issuer}/api/licence/update/{kid}", timeout=15).json()
    except Exception as exc:
        raise HTTPException(502, f"Could not reach your supplier: {exc}")
    if not info.get("available"):
        raise HTTPException(409, "No update is being offered.")
    try:
        manifest = updates.verify(info.get("manifest", ""),
                                  settings.license_public_key_hex)
    except updates.UpdateError as exc:
        raise HTTPException(422, str(exc))

    staging = updates.staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    blob = staging / (manifest.get("filename") or "update.zip")
    try:
        with requests.get(info["url"], stream=True, timeout=600) as resp:
            resp.raise_for_status()
            with open(blob, "wb") as fh:
                for chunk in resp.iter_content(updates.CHUNK):
                    fh.write(chunk)
    except Exception as exc:
        raise HTTPException(502, f"The download did not finish: {exc}")

    got = updates.digest(blob)
    if got != manifest["sha256"]:
        blob.unlink(missing_ok=True)
        raise HTTPException(
            422, "The download does not match what your supplier signed, so it "
                 "has been discarded. Nothing has been changed. Try again.")

    try:
        staged = updates.unpack(blob, staging / "unpacked")
        script = updates.apply_staged(staged)
    except updates.UpdateError as exc:
        raise HTTPException(422, str(exc))

    audit(db, user.id, "update_install", "release", 0,
          {"label": manifest["version"]}, request=request)

    # The helper is already waiting for this process to disappear. Stopping is the
    # last thing this request does, after the response has been handed back.
    import threading
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return {"installing": manifest["version"], "script": str(script),
            "message": "Installing now — this window will close and reopen on "
                       "the new version."}
