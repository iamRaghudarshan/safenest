"""Create the first FinMate administrator, or report how many users exist.

Used by the bundle's setup.py, but fine to run by hand:

    python create_admin.py --count                     # prints the number of users
    python create_admin.py "Name" you@example.com pw   # creates an admin
    python create_admin.py                             # asks interactively

Creating an admin whose email already exists updates that account's password and
promotes it to admin — which doubles as a password reset when you're locked out.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import User, UserModule  # noqa: E402
from app.security import check_password_strength, hash_password  # noqa: E402

# Every module an admin should be able to reach. Admins bypass the per-module RBAC
# checks, but the rows keep the Admin screen showing a truthful picture.
MODULES = ["loans", "cards", "insurance", "investments", "expenses",
           "reminders", "todo", "vault", "gallery", "documents"]


def count_users() -> int:
    Base.metadata.create_all(bind=engine)  # a brand-new SQLite file has no tables yet
    db = SessionLocal()
    try:
        return db.query(User).count()
    finally:
        db.close()


def create(name: str, email: str, password: str) -> str:
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise SystemExit(f"'{email}' does not look like an email address")
    check_password_strength(password)  # same rule the app enforces everywhere

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        now = datetime.now()
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.name = name
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.status = "active"
            # Retire every token issued under the old password.
            user.token_version = int(user.token_version or 0) + 1
            action = "updated"
        else:
            user = User(name=name, email=email, password_hash=hash_password(password),
                        role="admin", status="active", token_version=0,
                        failed_logins=0, two_factor_enabled=0,
                        created_at=now, updated_at=now)
            db.add(user)
            action = "created"
        db.commit()
        db.refresh(user)

        have = {m for (m,) in db.query(UserModule.module_key)
                .filter(UserModule.user_id == user.id).all()}
        for key in MODULES:
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
        print(count_users())
        return 0

    if len(args) >= 3:
        name, email, password = args[0], args[1], " ".join(args[2:])
    else:
        print("Create a FinMate administrator\n")
        name = input("  Name: ").strip() or "Admin"
        email = input("  Email: ").strip()
        import getpass
        password = getpass.getpass("  Password (12+ characters): ")

    try:
        action = create(name, email, password)
    except SystemExit:
        raise
    except Exception as exc:
        # HTTPException from check_password_strength carries the readable reason.
        print(f"Failed: {getattr(exc, 'detail', exc)}")
        return 1
    print(f"Administrator {action}: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
