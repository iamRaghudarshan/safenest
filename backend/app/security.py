from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
# PyJWT rather than python-jose. python-jose pulls in `ecdsa`, which carries an
# unfixed Minerva timing vulnerability (PYSEC-2026-1325) that its maintainers
# have declared out of scope. This app only ever signs with HS256, so the flawed
# code was never reached — but a dependency with a permanent known-vulnerable
# status is not worth carrying for a library doing two lines of work. PyJWT does
# the same job and drops ecdsa, rsa and pyasn1 with it.
import jwt
from jwt import PyJWTError
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User, UserModule

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(p: str) -> str:
    # bcrypt has a 72-byte cap; PHP-created $2y$ hashes verify fine here too
    return bcrypt.hashpw(p.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode()[:72], h.encode())
    except Exception:
        return False


MIN_PASSWORD_LEN = 12


def check_password_strength(pw: str) -> None:
    """Reject passwords that are trivially guessable. Length does most of the work;
    the character-class rule just stops 'aaaaaaaaaaaa'."""
    if len(pw or "") < MIN_PASSWORD_LEN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(set(pw)) < 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Password is too repetitive — mix in more distinct characters")


def create_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "ver": int(user.token_version or 0),  # invalidated when the user's version moves on
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def get_current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        uid = int(payload.get("sub"))
        ver = int(payload.get("ver", -1))
    except (PyJWTError, TypeError, ValueError):
        raise cred_exc
    user = db.query(User).get(uid)
    if not user or user.status != "active":
        raise cred_exc
    # A password change / admin reset bumps token_version, retiring every older token.
    if ver != int(user.token_version or 0):
        raise cred_exc
    # The role is re-read from the database on every request, so a demotion takes
    # effect immediately instead of lingering until the token expires.
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    if not licence_grants_admin(user):
        # A licensed copy has no administrator by design (see §10). The startup
        # sweep in main.py corrects the rows, but a sweep is a tidy-up and this is
        # the guarantee: whatever a row says, the signed licence decides, and it
        # decides on every request rather than once at boot.
        #
        # It matters because the row could be wrong. A customer was made an admin
        # by a first-run bug, and an admin can create more admins -- accounts the
        # sweep does not know to look for. This closes that off whether or not
        # anything was ever tidied.
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This copy is licensed to be used, not administered.")
    return user


def licence_grants_admin(user: User) -> bool:
    """May this account act as an administrator on this installation?

    True everywhere except a licensed copy, where the Ed25519 licence is the
    authority: only the person it names, and only if it says `role: admin`.
    Nothing a customer can edit takes part in this decision.
    """
    if not settings.licensed_mode:
        return True
    from . import licensing
    payload = licensing.parse(licensing.read_token(settings.license_path),
                              settings.license_public_key_hex) or {}
    if payload.get("state") in (licensing.INVALID, licensing.MISSING):
        return False
    email = (payload.get("email") or "").strip().lower()
    return bool(email and payload.get("role") == "admin"
                and (user.email or "").strip().lower() == email)


def guard(module: str, action: str = "view"):
    """Dependency factory enforcing per-module RBAC (admins bypass)."""

    def _dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if user.role == "admin":
            return user
        perm = (
            db.query(UserModule)
            .filter(UserModule.user_id == user.id, UserModule.module_key == module)
            .first()
        )
        if not perm or not getattr(perm, f"can_{action}"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"You don't have permission to {action} {module}")
        return user

    return _dep
