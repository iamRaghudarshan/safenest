"""Generic owner-scoped, RBAC-guarded CRUD for the simpler finance modules."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Insurance, Investment, User
from ..security import get_current_user, guard


def _label(obj, cfg: dict) -> str:
    """A name a person recognises, so the activity log doesn't just say 'policy 7'."""
    for field in cfg.get("label_fields", ()):
        value = getattr(obj, field, None)
        if value:
            return str(value)[:80]
    return f"{cfg['entity']} {obj.id}"

router = APIRouter(prefix="/api", tags=["resources"])

# Per module: the model, the fields a client may set, list ordering, the entity
# name used in audit rows, which fields are genuinely mandatory, and defaults for
# NOT NULL columns a user may reasonably leave blank.
#
# Without `defaults` an omitted NOT NULL column reaches MySQL and comes back as a
# bare 500 — e.g. saving an investment with no broker, which is perfectly normal.
CONFIG: dict[str, dict] = {
    "insurance": {
        "model": Insurance,
        "fields": ["policy_type", "provider", "policy_no", "premium", "sum_assured",
                   "frequency", "renewal_date"],
        "order": ("renewal_date", "asc"),
        "entity": "policy",
        "required": {"provider": "Provider is required"},
        "defaults": {"policy_type": "Other", "frequency": "yearly"},
        "label_fields": ("provider", "policy_no"),
    },
    "investments": {
        "model": Investment,
        "fields": ["broker", "invest_type", "name", "invested_amount", "current_value",
                   "units", "maturity_date"],
        "order": ("current_value", "desc"),
        "entity": "investment",
        "required": {"name": "Name is required"},
        "defaults": {"invest_type": "Other", "broker": ""},
        "label_fields": ("name", "broker"),
    },
}


def _clean(data: dict, cfg: dict, *, creating: bool) -> dict:
    """Validate and normalise an incoming payload for one module."""
    if creating:
        for field, message in cfg["required"].items():
            if not str(data.get(field) or "").strip():
                raise HTTPException(422, message)
    out = {f: data[f] for f in cfg["fields"] if f in data and data[f] not in ("", None)}
    if creating:
        for field, fallback in cfg["defaults"].items():
            if field not in out:
                out[field] = fallback
    return out


def _make(module: str):
    cfg = CONFIG[module]
    Model, entity = cfg["model"], cfg["entity"]
    order_col, order_dir = cfg["order"]

    def index(user: User = Depends(guard(module, "view")), db: Session = Depends(get_db)):
        q = db.query(Model).filter(Model.user_id == user.id)
        col = getattr(Model, order_col)
        q = q.order_by(col.desc() if order_dir == "desc" else col.asc())
        return {"items": [to_dict(r) for r in q.limit(500).all()]}

    def create(body: dict = Body(...), user: User = Depends(guard(module, "create")), db: Session = Depends(get_db)):
        values = _clean(body, cfg, creating=True)
        obj = Model(user_id=user.id, created_at=ist.now(), updated_at=ist.now(), **values)
        db.add(obj); db.commit(); db.refresh(obj)
        audit(db, user.id, "create", entity, obj.id, {"label": _label(obj, cfg)})
        return {"item": to_dict(obj)}

    def update(id: int, body: dict = Body(...), user: User = Depends(guard(module, "edit")), db: Session = Depends(get_db)):
        obj = db.query(Model).filter(Model.id == id, Model.user_id == user.id).first()
        if not obj:
            raise HTTPException(404, f"{entity} not found")
        before = snapshot(obj)
        for field, value in _clean(body, cfg, creating=False).items():
            setattr(obj, field, value)
        obj.updated_at = ist.now()
        db.commit(); db.refresh(obj)
        diff = changes(before, snapshot(obj))
        audit(db, user.id, "update", entity, id,
              {"label": _label(obj, cfg), "changes": diff} if diff else {"label": _label(obj, cfg)})
        return {"item": to_dict(obj)}

    def delete(id: int, user: User = Depends(guard(module, "delete")), db: Session = Depends(get_db)):
        obj = db.query(Model).filter(Model.id == id, Model.user_id == user.id).first()
        if not obj:
            raise HTTPException(404, f"{entity} not found")
        label = _label(obj, cfg)
        db.delete(obj); db.commit()
        audit(db, user.id, "delete", entity, id, {"label": label})
        return {"deleted": id}

    return index, create, update, delete


for _mod in CONFIG:
    _i, _c, _u, _d = _make(_mod)
    router.get(f"/{_mod}")(_i)
    router.post(f"/{_mod}")(_c)
    router.put(f"/{_mod}/{{id}}")(_u)
    router.delete(f"/{_mod}/{{id}}")(_d)
