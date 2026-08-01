"""Create one ordinary account, with every module granted.

    python create_account.py "Meera Nair" meera@example.com "the password" user

Used by the setup of a licensed copy, where there is no administrator to create
and exactly one person is meant to sign in. create_admin.py deliberately always
makes an administrator, which is the wrong thing entirely for a copy sold to
somebody — they would be able to issue themselves permissions, add accounts, and
export everyone's data on a machine that is supposed to be a single-user product.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import ist                                            # noqa: E402
from app.database import Base, SessionLocal, engine            # noqa: E402
from app.models import User, UserModule                        # noqa: E402
from app.routers.auth import ALL_MODULES                       # noqa: E402
from app.security import check_password_strength, hash_password  # noqa: E402


def create(name: str, email: str, password: str, role: str = "user") -> str:
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not name:
        raise SystemExit("A name is required")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise SystemExit(f"'{email}' does not look like an email address")
    if role not in ("user", "admin"):
        raise SystemExit("role must be 'user' or 'admin'")
    check_password_strength(password)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        now = ist.now()
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.name = name
            user.password_hash = hash_password(password)
            user.role = role
            user.status = "active"
            user.token_version = int(user.token_version or 0) + 1
            action = "updated"
        else:
            user = User(name=name, email=email, password_hash=hash_password(password),
                        role=role, status="active", token_version=0, failed_logins=0,
                        two_factor_enabled=0, created_at=now, updated_at=now)
            db.add(user)
            action = "created"
        db.commit(); db.refresh(user)

        # Admins bypass the per-module checks, so rows are only meaningful for a
        # plain user — but granting them is harmless and keeps the two paths alike.
        have = {m for (m,) in db.query(UserModule.module_key)
                .filter(UserModule.user_id == user.id).all()}
        for key in ALL_MODULES:
            if key not in have:
                db.add(UserModule(user_id=user.id, module_key=key,
                                  can_view=1, can_create=1, can_edit=1, can_delete=1))
        db.commit()
        return action
    finally:
        db.close()


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--count":
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            print(db.query(User).count())
        finally:
            db.close()
        return 0
    if len(args) < 3:
        print(__doc__)
        return 2
    name, email, password = args[0], args[1], args[2]
    role = args[3] if len(args) > 3 else "user"
    action = create(name, email, password, role)
    print(f"{action}: {email} ({role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
