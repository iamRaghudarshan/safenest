import re
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import ist
from .. import accounts
from ..database import get_db
from ..helpers import audit
from ..models import User, UserModule
from ..routers.auth import ALL_MODULES, public_user
from ..security import check_password_strength, hash_password, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Deliberately permissive — enough to reject typos and obvious junk without
# rejecting valid-but-unusual addresses.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _valid_email(value: str) -> str:
    email = (value or "").strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address")
    return email


@router.get("/users")
def users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    out = []
    for u in db.query(User).order_by(User.id.asc()).all():
        granted = len(ALL_MODULES) if u.role == "admin" else \
            db.query(UserModule).filter(UserModule.user_id == u.id, UserModule.can_view == 1).count()
        out.append(public_user(u) | {
            "modules_granted": granted,
            "last_login": u.last_login_at.strftime("%d-%m-%Y %H:%M") if u.last_login_at else None,
        })
    return {"users": out, "allModules": ALL_MODULES}


@router.post("/users")
def create_user(request: Request, body: dict = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = (body.get("name") or "").strip()
    pw = body.get("password") or ""
    role = body.get("role") if body.get("role") in ("admin", "user") else "user"
    if not name:
        raise HTTPException(422, "Name is required")
    email = _valid_email(body.get("email"))
    check_password_strength(pw)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "A user with that email already exists")
    now = ist.now()
    u = User(name=name, email=email, password_hash=hash_password(pw), role=role,
             status=body.get("status", "active"), created_at=now, updated_at=now)
    db.add(u); db.commit(); db.refresh(u)
    if role == "user":
        # An optional permissions matrix lets the admin choose access AT creation,
        # instead of always granting everything and then trimming it in a second
        # step. Absent (the long-standing behaviour) → full access to every module.
        perms = body.get("permissions")
        for m in ALL_MODULES:
            if perms is None:
                v = c = e = d = 1
            else:
                p = perms.get(m) or {}
                v = 1 if p.get("view") else 0
                c = 1 if p.get("create") else 0
                e = 1 if p.get("edit") else 0
                d = 1 if p.get("delete") else 0
            db.add(UserModule(user_id=u.id, module_key=m,
                              can_view=v, can_create=c, can_edit=e, can_delete=d))
        db.commit()
    audit(db, admin.id, "user_create", "user", u.id,
          # Name and email are recorded here so the log still identifies the
          # account after it is deleted and its row is gone.
          {"label": u.name or u.email, "email": u.email, "role": role}, request=request)
    return {"id": u.id}


@router.put("/users/{id}")
def update_user(id: int, request: Request, body: dict = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).get(id)
    if not u:
        raise HTTPException(404, "User not found")
    if body.get("name"):
        u.name = body["name"].strip()
    if body.get("email"):
        email = _valid_email(body["email"])
        other = db.query(User).filter(User.email == email).first()
        if other and other.id != id:
            raise HTTPException(409, "That email is already in use")
        u.email = email
    if body.get("role") in ("admin", "user") and body["role"] != u.role:
        if id == admin.id:
            raise HTTPException(422, "You can't change your own role")
        u.role = body["role"]
        # A demoted admin must not keep an admin-shaped session alive.
        u.token_version = int(u.token_version or 0) + 1
    if body.get("status") in ("active", "suspended"):
        if id == admin.id and body["status"] == "suspended":
            raise HTTPException(422, "You can't suspend your own account")
        u.status = body["status"]
    if body.get("password"):
        check_password_strength(body["password"])
        u.password_hash = hash_password(body["password"])
        u.failed_logins = 0
        u.locked_until = None
        # An admin reset exists to lock an attacker out — so kill their sessions too.
        u.token_version = int(u.token_version or 0) + 1
    db.commit()
    audit(db, admin.id, "user_update", "user", id,
          {"label": u.name or u.email, "email": u.email,
           "password_reset": bool(body.get("password")), "role": body.get("role"),
           "status": body.get("status")}, request=request)
    return {"id": id}


@router.delete("/users/{id}")
def delete_user(id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if id == admin.id:
        raise HTTPException(422, "You can't delete your own account")
    u = db.query(User).get(id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.role == "admin" and db.query(User).filter(User.role == "admin").count() <= 1:
        raise HTTPException(422, "Cannot delete the last admin")
    email = u.email
    # Remove everything the account owned — rows AND stored files — so no photo,
    # ID scan or saved password outlives the user it belonged to.
    removed = accounts.purge(db, id)
    db.delete(u); db.commit()
    audit(db, admin.id, "user_delete", "user", id,
          {"label": email, "email": email, "removed": removed}, request=request)
    return {"deleted": id, "removed": removed}


@router.get("/users/{id}/data")
def user_data(id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """What deleting this account would destroy — so the confirmation isn't blind."""
    if not db.query(User).get(id):
        raise HTTPException(404, "User not found")
    return accounts.summarise(db, id)


@router.get("/users/{id}/permissions")
def permissions(id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = {r.module_key: r for r in db.query(UserModule).filter(UserModule.user_id == id).all()}
    matrix = {}
    for m in ALL_MODULES:
        r = rows.get(m)
        matrix[m] = {"view": bool(r and r.can_view), "create": bool(r and r.can_create),
                     "edit": bool(r and r.can_edit), "delete": bool(r and r.can_delete)}
    return {"userId": id, "matrix": matrix}


@router.post("/permissions")
def toggle_permission(request: Request, body: dict = Body(...), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    uid = int(body.get("userId", 0))
    module = body.get("module", "")
    action = body.get("action", "view")
    value = 1 if body.get("value") else 0
    if not uid or module not in ALL_MODULES or action not in ("view", "create", "edit", "delete"):
        raise HTTPException(422, "Invalid request")
    r = db.query(UserModule).filter(UserModule.user_id == uid, UserModule.module_key == module).first()
    if r:
        setattr(r, f"can_{action}", value)
    else:
        r = UserModule(user_id=uid, module_key=module, can_view=0, can_create=0, can_edit=0, can_delete=0)
        setattr(r, f"can_{action}", value)
        db.add(r)
    db.commit()
    audit(db, admin.id, "permission_change", "user", uid,
          {"module": module, "action": action, "value": value}, request=request)
    return {"userId": uid, "module": module, "action": action, "value": bool(value)}


@router.get("/site-stats")
def site_stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """How many times the public site/download page has been opened."""
    from sqlalchemy import func
    from ..models import SiteStat
    rows = db.query(SiteStat).order_by(SiteStat.day.desc()).limit(30).all()
    total = int(db.query(func.coalesce(func.sum(SiteStat.visits), 0)).scalar() or 0)
    today = ist.today().isoformat()
    today_n = int(next((r.visits for r in rows if r.day == today), 0) or 0)
    return {"total": total, "today": today_n,
            "days": [{"day": r.day, "visits": int(r.visits or 0)} for r in reversed(rows)]}
