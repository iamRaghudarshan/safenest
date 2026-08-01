import json
from datetime import date, datetime
from decimal import Decimal

from fastapi import Request
from sqlalchemy.orm import Session

from . import ist
from .models import AuditLog
from .ratelimit import client_ip


def to_dict(obj) -> dict:
    out = {}
    for col in obj.__table__.columns:
        v = getattr(obj, col.name)
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, (date, datetime)):
            v = v.isoformat()[:10] if isinstance(v, date) and not isinstance(v, datetime) else (v.isoformat() if v else None)
        out[col.name] = v
    return out


# Fields whose CONTENT must never reach the log. A change to them is recorded, but
# the values are not — an audit trail that quotes the password someone just set is
# worse than no trail at all.
_SECRET_FIELDS = {"password", "password_hash", "password_enc", "notes_enc",
                  "vault_recovery_hash", "token", "embedding", "endpoint",
                  "p256dh", "auth", "content_hash", "phash"}
# Fields that change on every write and would drown the useful entries.
_NOISE_FIELDS = {"id", "user_id", "created_at", "updated_at"}

_REDACTED = "•••"


def _short(value) -> str | None:
    """One-line, bounded representation — audit rows are read, not parsed."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= 80 else text[:79] + "…"


def snapshot(obj) -> dict:
    """Column values of an ORM row, for comparing before and after an edit."""
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def changes(before: dict, after: dict) -> dict:
    """`{field: [old, new]}` for what actually changed.

    This is what turns "someone updated loan 3" into "someone changed the EMI from
    12,000 to 12,500" — the difference between a log that proves an action happened
    and one that shows what it did.
    """
    out: dict[str, list] = {}
    for key, new in after.items():
        if key in _NOISE_FIELDS:
            continue
        old = before.get(key)
        if old == new:
            continue
        if key in _SECRET_FIELDS:
            out[key] = [_REDACTED, _REDACTED]
        else:
            out[key] = [_short(old), _short(new)]
    return out


def audit(db: Session, user_id, action, entity=None, entity_id=None, meta=None,
          request: Request | None = None):
    """Append an audit row. Pass `request` on security-relevant actions (logins,
    permission changes, vault reveals) so the origin IP and client are recorded —
    without them the log can't answer 'who did this, from where'."""
    ip = ua = None
    if request is not None:
        ip = client_ip(request)[:45]
        ua = (request.headers.get("user-agent") or "")[:255] or None
    db.add(AuditLog(
        user_id=user_id, action=action, entity=entity, entity_id=entity_id,
        ip=ip, user_agent=ua,
        meta=json.dumps(meta) if meta else None, created_at=ist.now(),
    ))
    db.commit()
