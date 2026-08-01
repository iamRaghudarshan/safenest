from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import ist
from ..crypto import decrypt, encrypt
from ..database import get_db
from ..helpers import audit
from ..models import User, VaultItem
from ..ratelimit import rate_limit
from ..security import guard

router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.get("")
def index(user: User = Depends(guard("vault", "view")), db: Session = Depends(get_db)):
    rows = db.query(VaultItem).filter(VaultItem.user_id == user.id).order_by(VaultItem.title.asc()).all()
    items = [{"id": r.id, "title": r.title, "username": r.username, "url": r.url,
              "category": r.category, "has_password": bool(r.password_enc)} for r in rows]
    return {"items": items}


@router.post("/{id}/reveal")
def reveal(id: int, request: Request, response: Response,
           user: User = Depends(guard("vault", "view")), db: Session = Depends(get_db)):
    # Throttled so a stolen token can't be used to siphon the whole vault at once,
    # and every reveal is recorded with its origin IP.
    rate_limit(request, f"vault-reveal:{user.id}", limit=20, window=300)
    r = db.query(VaultItem).filter(VaultItem.id == id, VaultItem.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "Item not found")
    if not r.password_enc:
        raise HTTPException(404, "No password stored for this item")
    try:
        plain = decrypt(r.password_enc)
    except Exception:
        raise HTTPException(500, "Cannot decrypt (item was encrypted by the old app — re-save it)")
    audit(db, user.id, "reveal", "vault", id, {"label": r.title}, request=request)
    response.headers["Cache-Control"] = "no-store"
    return {"id": id, "password": plain}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("vault", "create")), db: Session = Depends(get_db)):
    if not (body.get("title") or "").strip():
        raise HTTPException(422, "Title is required")
    now = ist.now()
    r = VaultItem(user_id=user.id, title=body["title"].strip(), username=body.get("username"),
                  url=body.get("url"), category=body.get("category"),
                  password_enc=encrypt(body["password"]) if body.get("password") else None,
                  created_at=now, updated_at=now)
    db.add(r); db.commit(); db.refresh(r)
    audit(db, user.id, "create", "vault", r.id, {"label": r.title})
    return {"id": r.id}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("vault", "edit")), db: Session = Depends(get_db)):
    r = db.query(VaultItem).filter(VaultItem.id == id, VaultItem.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "Item not found")
    for f in ["title", "username", "url", "category"]:
        if f in body:
            setattr(r, f, body[f])
    if body.get("password"):
        r.password_enc = encrypt(body["password"])
    r.updated_at = ist.now(); db.commit()
    return {"id": id}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("vault", "delete")), db: Session = Depends(get_db)):
    r = db.query(VaultItem).filter(VaultItem.id == id, VaultItem.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "Item not found")
    db.delete(r); db.commit()
    return {"deleted": id}
