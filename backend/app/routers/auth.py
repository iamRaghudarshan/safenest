import io
import os
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import ist
from .. import storage
from ..database import get_db
from ..helpers import audit
from ..models import User, UserModule
from ..ratelimit import rate_limit
from ..security import (check_password_strength, create_token, get_current_user,
                        hash_password, verify_password)
from ..signing import sign, verify

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15
AVATAR_MAX = 8 * 1024 * 1024  # 8 MB upload cap
AVATAR_PX = 320               # stored size; avatars are only ever shown small
ALL_MODULES = ["loans", "cards", "insurance", "investments", "expenses", "reminders", "todo", "gallery", "vault", "documents"]

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

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = ist.now()
    db.commit()
    audit(db, user.id, "login", "user", user.id, request=request)
    return {"token": create_token(user), "user": public_user(user), "modules": modules_for(user, db)}


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
