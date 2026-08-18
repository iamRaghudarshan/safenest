import hmac
import io
import json
import os
import uuid
from datetime import datetime, timedelta

from fastapi import (APIRouter, Body, Depends, File, HTTPException, Request,
                     UploadFile, status)
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import crypto, ist, totp
from .. import storage
from ..database import get_db
from ..helpers import audit
from ..models import User, UserModule
from ..ratelimit import rate_limit
from ..security import (check_password_strength, create_2fa_challenge,
                        create_token, get_current_user, hash_password,
                        licence_grants_admin, read_2fa_challenge,
                        verify_password)
from ..signing import sign, verify

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15
AVATAR_MAX = 8 * 1024 * 1024  # 8 MB upload cap
AVATAR_PX = 320               # stored size; avatars are only ever shown small
ALL_MODULES = ["loans", "cards", "insurance", "investments", "expenses", "reminders", "todo", "habits", "gallery", "vault", "documents"]

# Compared against when the email doesn't exist, so a miss costs the same bcrypt
# work as a hit — otherwise response timing alone reveals which emails are real.
_DUMMY_HASH = hash_password("timing-equalisation-placeholder")


class LoginIn(BaseModel):
    email: str
    password: str


def public_user(u: User) -> dict:
    parts = (u.name or "").split()
    initials = ((parts[0][:1] if parts else "") + (parts[-1][:1] if len(parts) > 1 else "")).upper()
    avatar_url = (f"/api/auth/avatar/{u.id}/{u.avatar}?t={sign(u.id, f'avatar/{u.avatar}')}"
                  if u.avatar else None)
    return {
        "id": u.id, "name": u.name, "email": u.email, "role": u.role,
        "status": u.status, "phone": u.phone, "initials": initials,
        "avatar_url": avatar_url,
        # What this account may ACTUALLY do, not what its row says. The screens
        # were reading `role` and drawing User management, Licences and the
        # whole-installation export from it -- so an account whose row said admin
        # kept being offered all of it while every one of those calls came back
        # 403. Buttons that cannot work are worse than no buttons: the customer
        # reads them as things the supplier can do on their machine.
        #
        # Same rule as hosting.can_manage: the server decides, the screen asks.
        # Working it out in the frontend is how the two halves drift apart.
        "can_admin": licence_grants_admin(u) and u.role == "admin",
    }


def modules_for(u: User, db: Session) -> list[str]:
    if u.role == "admin":
        return ALL_MODULES
    rows = db.query(UserModule).filter(UserModule.user_id == u.id, UserModule.can_view == 1).all()
    return [r.module_key for r in rows]


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    # Per-IP throttle (on top of the per-account lockout below) — 10 tries / 5 min.
    rate_limit(request, "login", limit=10, window=300)
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if not user:
        verify_password(body.password, _DUMMY_HASH)  # constant-ish work on a miss
        audit(db, None, "login_failed", "user", None, {"email": email[:190]}, request=request)
        raise invalid

    if not verify_password(body.password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_ATTEMPTS:
            user.locked_until = ist.now() + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        db.commit()
        audit(db, user.id, "login_failed", "user", user.id, request=request)
        raise invalid

    # The password is correct from here on, so account-state messages no longer
    # leak anything to a stranger — only someone who already proved they know it.
    if user.locked_until and user.locked_until > ist.now():
        mins = int((user.locked_until - ist.now()).total_seconds() // 60) + 1
        audit(db, user.id, "login_locked", "user", user.id, request=request)
        raise HTTPException(status.HTTP_423_LOCKED, f"Account locked. Try again in {mins} min.")
    if user.status != "active":
        audit(db, user.id, "login_suspended", "user", user.id, request=request)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is suspended. Contact your administrator.")

    # THE SECOND FACTOR, if this account has one.
    #
    # No session token is issued here — only a short-lived challenge that says
    # "this password was right, now prove the other thing". Handing out the real
    # token and checking the code afterwards would make the second factor
    # decorative: anyone who could read the response already had the session.
    if user.two_factor_enabled and user.totp_secret_enc:
        user.failed_logins = 0
        user.locked_until = None
        db.commit()
        audit(db, user.id, "login_2fa_challenge", "user", user.id, request=request)
        return {
            "two_factor": True,
            "challenge": create_2fa_challenge(user),
            # Named so the screen can offer the right thing, and so somebody
            # who has lost their phone is not left guessing that a recovery
            # code goes in the same box.
            "methods": ["totp", "recovery"],
        }

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = ist.now()
    db.commit()
    audit(db, user.id, "login", "user", user.id, request=request)
    return {"token": create_token(user), "user": public_user(user), "modules": modules_for(user, db)}


@router.post("/login/2fa")
def login_2fa(body: dict = Body(...), request: Request = None,
              db: Session = Depends(get_db)):
    """Finish a sign-in that asked for a code.

    Rate limited harder than the password step: six digits is 1,000,000
    possibilities, and an unthrottled endpoint turns that into an afternoon's
    work. The challenge is single-use and short-lived, so an attacker cannot
    keep one and grind at it either.
    """
    rate_limit(request, "login-2fa", limit=8, window=300)
    challenge = str(body.get("challenge") or "")
    code = str(body.get("code") or "")
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "That code is not right. Try the next one.")

    uid = read_2fa_challenge(challenge)
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "That sign-in expired. Enter your password again.")
    user = db.query(User).filter(User.id == uid).first()
    if not user or not user.two_factor_enabled or not user.totp_secret_enc:
        raise invalid

    if _consume_second_factor(db, user, code):
        user.last_login_at = ist.now()
        user.failed_logins = 0
        db.commit()
        audit(db, user.id, "login", "user", user.id, request=request)
        return {"token": create_token(user), "user": public_user(user),
                "modules": modules_for(user, db)}

    # A wrong code counts towards the same lockout the password uses. Otherwise
    # the second factor would be the one door with no limit behind it.
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= MAX_ATTEMPTS:
        user.locked_until = ist.now() + timedelta(minutes=LOCK_MINUTES)
        user.failed_logins = 0
    db.commit()
    audit(db, user.id, "login_2fa_failed", "user", user.id, request=request)
    raise invalid


def _consume_second_factor(db: Session, user: User, code: str) -> bool:
    """True if `code` is a valid TOTP or an unused recovery code.

    A recovery code is REMOVED as it is accepted — that is what makes it single
    use, and it is the difference between a spare key and a copied one.
    """
    secret = crypto.decrypt(user.totp_secret_enc) if user.totp_secret_enc else ""
    if secret and totp.verify(secret, code):
        return True

    stored = json.loads(user.recovery_codes or "[]")
    wanted = totp.hash_recovery(code)
    # compare_digest against each, rather than `in`, so the check does not leak
    # timing about how many codes remain or how close one was.
    for i, h in enumerate(stored):
        if hmac.compare_digest(h, wanted):
            stored.pop(i)
            user.recovery_codes = json.dumps(stored)
            db.commit()
            return True
    return False


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"user": public_user(user), "modules": modules_for(user, db)}


class ProfileIn(BaseModel):
    name: str


@router.put("/profile")
def update_profile(body: ProfileIn, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Users may rename themselves but NOT change their own email — the address is
    the login identity and the admin's handle on the account, so only an admin can
    move it. Role and status are likewise off-limits here."""
    name = (body.name or "").strip()
    if len(name) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name must be at least 2 characters")
    if len(name) > 120:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name is too long")
    user.name = name
    user.updated_at = ist.now()
    db.commit()
    audit(db, user.id, "profile_update", "user", user.id, request=request)
    return {"user": public_user(user)}


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), request: Request = None,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Store a square, downscaled JPEG as the user's profile picture."""
    raw = await file.read(AVATAR_MAX + 1)
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    if len(raw) > AVATAR_MAX:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Image too large (max 8 MB)")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "That file isn’t a readable image")

    # Centre-crop to a square, then downscale — avatars are only ever shown small.
    img = img.convert("RGB")
    side = min(img.width, img.height)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((AVATAR_PX, AVATAR_PX), Image.LANCZOS)
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=88)

    name = f"{uuid.uuid4().hex}.jpg"
    old = user.avatar
    storage.save(storage.AVATARS, user.id, storage.ORIGINAL, name, buf.getvalue())
    user.avatar = name
    user.updated_at = ist.now()
    db.commit()
    if old and old != name:
        storage.remove(storage.AVATARS, user.id, storage.ORIGINAL, old)
    audit(db, user.id, "avatar_update", "user", user.id, request=request)
    return {"user": public_user(user)}


@router.delete("/avatar")
def remove_avatar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.avatar:
        storage.remove(storage.AVATARS, user.id, storage.ORIGINAL, user.avatar)
    user.avatar = None
    user.updated_at = ist.now()
    db.commit()
    return {"user": public_user(user)}


@router.get("/avatar/{user_id}/{name}")
def get_avatar(user_id: int, name: str, t: str = "", db: Session = Depends(get_db)):
    """Signed, expiring URL so an <img> tag can load it without a bearer header."""
    if not storage.is_safe_name(name) or not verify(user_id, f"avatar/{name}", t):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    path = storage.media_path(storage.AVATARS, user_id, storage.ORIGINAL, name)
    if not os.path.isfile(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(path, media_type="image/jpeg", content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=3600"})


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePwIn, request: Request,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Throttled: this endpoint is an oracle for the current password.
    rate_limit(request, "change-pw", limit=5, window=300)
    if not verify_password(body.current_password, user.password_hash):
        audit(db, user.id, "password_change_failed", "user", user.id, request=request)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    check_password_strength(body.new_password)
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "New password must be different")
    user.password_hash = hash_password(body.new_password)
    # Retire every token minted before this change — including any an attacker holds.
    user.token_version = int(user.token_version or 0) + 1
    user.updated_at = ist.now()
    db.commit()
    audit(db, user.id, "password_change", "user", user.id, request=request)
    # Hand back a fresh token so the caller isn't logged out by its own change.
    return {"ok": True, "token": create_token(user)}


# ------------------------------------------------------------ two-factor ---
#
# Turned on by the account holder, for their own account, and never by an
# administrator for somebody else: a second factor somebody else set up is a
# second factor somebody else holds.


@router.get("/2fa")
def two_factor_status(user: User = Depends(get_current_user)):
    """Whether it is on, and how many recovery codes are left."""
    left = len(json.loads(user.recovery_codes or "[]"))
    return {
        "enabled": bool(user.two_factor_enabled),
        "recovery_left": left,
        "since": user.two_factor_at.isoformat() if user.two_factor_at else None,
    }


@router.post("/2fa/setup")
def two_factor_setup(request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Make a secret and hand back what an authenticator app needs.

    NOT enabled yet. The secret is stored so the code they type next can be
    checked against it, but two_factor_enabled stays 0 until they prove the app
    is actually working — otherwise a mistyped setup locks somebody out of
    their own records with no way back in.
    """
    rate_limit(request, f"2fa-setup:{user.id}", limit=10, window=600)
    if user.two_factor_enabled:
        raise HTTPException(409, "Two-step sign-in is already on for this account.")

    secret = totp.new_secret()
    user.totp_secret_enc = crypto.encrypt(secret)
    db.commit()

    from .branding import app_name
    return {
        "secret": secret,                     # shown once, for typing by hand
        "uri": totp.provisioning_uri(secret, user.email, app_name(db)),
        "digits": totp.DIGITS,
        "period": totp.STEP,
    }


@router.post("/2fa/enable")
def two_factor_enable(body: dict = Body(...), request: Request = None,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Prove the app works, then turn it on and hand over the recovery codes."""
    rate_limit(request, f"2fa-enable:{user.id}", limit=10, window=600)
    if not user.totp_secret_enc:
        raise HTTPException(409, "Start the setup first.")
    secret = crypto.decrypt(user.totp_secret_enc)
    if not totp.verify(secret, str(body.get("code") or "")):
        raise HTTPException(422, "That code is not right. Check the time on your "
                                 "phone is set automatically, then try the next one.")

    codes = totp.new_recovery_codes()
    user.recovery_codes = json.dumps([totp.hash_recovery(c) for c in codes])
    user.two_factor_enabled = 1
    user.two_factor_at = ist.now()
    db.commit()
    audit(db, user.id, "2fa_enabled", "user", user.id, request=request)
    # The ONLY time these are readable. They are stored hashed, so this response
    # is the one chance to write them down — which the screen has to say.
    return {"enabled": True, "recovery_codes": codes}


@router.post("/2fa/disable")
def two_factor_disable(body: dict = Body(...), request: Request = None,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Turn it off — and ask for the PASSWORD to do it.

    A session token is enough for most things and deliberately not for this: an
    unlocked phone left on a table should not be able to remove the second
    factor from an account.
    """
    rate_limit(request, f"2fa-disable:{user.id}", limit=6, window=600)
    if not verify_password(str(body.get("password") or ""), user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That password is not right.")
    user.two_factor_enabled = 0
    user.totp_secret_enc = None
    user.recovery_codes = None
    user.two_factor_at = None
    db.commit()
    audit(db, user.id, "2fa_disabled", "user", user.id, request=request)
    return {"enabled": False}


@router.post("/2fa/recovery/new")
def two_factor_new_recovery(body: dict = Body(...), request: Request = None,
                            user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """A fresh set, when the old ones are used up or were seen by somebody.

    Also password-guarded, and it REPLACES the old set — leaving the previous
    codes valid would mean "regenerate" quietly added spare keys instead of
    changing the lock.
    """
    rate_limit(request, f"2fa-recovery:{user.id}", limit=6, window=600)
    if not user.two_factor_enabled:
        raise HTTPException(409, "Two-step sign-in is not on for this account.")
    if not verify_password(str(body.get("password") or ""), user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That password is not right.")
    codes = totp.new_recovery_codes()
    user.recovery_codes = json.dumps([totp.hash_recovery(c) for c in codes])
    db.commit()
    audit(db, user.id, "2fa_recovery_replaced", "user", user.id, request=request)
    return {"recovery_codes": codes}
