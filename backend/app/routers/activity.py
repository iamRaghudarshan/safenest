"""Activity log — the audit trail, made readable.

Every create, edit and delete already lands in `audit_logs` in MySQL. This turns
those rows into something a person can scan: what happened, to what, who did it,
and for edits, exactly which fields changed and what they went from and to.

Scope is the same rule used everywhere else in the app: you see your own activity,
an administrator sees everyone's. Nobody can see another user's entries otherwise,
because the labels alone would reveal what they name their accounts and policies.
"""
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..models import AuditLog, User
from ..security import get_current_user

router = APIRouter(prefix="/api/activity", tags=["activity"])

# How each action reads in the log, and the tone it's shown in.
ACTIONS = {
    "create": ("Added", "ok"), "update": ("Edited", "info"), "delete": ("Deleted", "danger"),
    "trash": ("Moved to trash", "warn"), "restore": ("Restored", "ok"),
    "empty_trash": ("Emptied trash", "danger"), "dedupe": ("Removed duplicates", "warn"),
    "upload": ("Uploaded", "ok"), "scan": ("Scanned", "ok"),
    "reindex": ("Rebuilt people grouping", "warn"),
    "done": ("Completed", "ok"), "reopen": ("Reopened", "info"),
    "loan_paid": ("Marked EMI paid", "ok"), "card_paid": ("Marked bill paid", "ok"),
    "tag_person": ("Tagged a person", "info"),
    "login": ("Signed in", "info"), "login_failed": ("Failed sign-in", "danger"),
    "login_locked": ("Account locked", "danger"),
    "login_suspended": ("Blocked sign-in (suspended)", "danger"),
    "password_change": ("Changed password", "warn"),
    "password_change_failed": ("Failed password change", "danger"),
    "profile_update": ("Updated profile", "info"),
    "avatar_update": ("Changed photo", "info"),
    "reveal": ("Revealed a vault secret", "warn"),
    "user_create": ("Created a user", "warn"), "user_update": ("Edited a user", "warn"),
    "user_delete": ("Deleted a user", "danger"),
    "permission_change": ("Changed permissions", "warn"),
    "push_subscribe": ("Enabled notifications", "info"),
    "purge_cdn": ("Purged the CDN cache", "info"),
    "export_bundle": ("Exported a copy", "warn"),
}

# Actions that are about the account rather than the data. Kept separate so
# "show me what changed in my records" isn't buried under sign-in noise.
SECURITY_ACTIONS = {
    "login", "login_failed", "login_locked", "login_suspended", "password_change",
    "password_change_failed", "permission_change", "reveal", "user_create",
    "user_update", "user_delete", "export_bundle", "purge_cdn", "profile_update",
    "avatar_update",
}

# Which entities belong to which part of the app. A module is what a person thinks
# in ("Photos"), an entity is what the row records ("photo", "album", "gallery") —
# so one module usually covers several.
MODULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "expenses":    ("Expenses",    ("expense",)),
    "loans":       ("Loans",       ("loan",)),
    "cards":       ("Cards",       ("card",)),
    "insurance":   ("Insurance",   ("policy",)),
    "investments": ("Investments", ("investment",)),
    "reminders":   ("Reminders",   ("reminder",)),
    "todo":        ("Tasks",       ("task",)),
    "gallery":     ("Photos",      ("photo", "gallery", "album")),
    "documents":   ("Documents",   ("document",)),
    "vault":       ("Vault",       ("vault",)),
    "masters":     ("Lists",       ("master",)),
    # "auth" is what sign-ins were recorded under before the entity was renamed to
    # "user"; those rows still exist, so the module has to claim both.
    "account":     ("Account",     ("user", "system", "auth")),
}
ENTITY_TO_MODULE = {e: key for key, (_, ents) in MODULES.items() for e in ents}

ENTITY_LABELS = {
    "loan": "Loan", "card": "Card", "policy": "Policy", "investment": "Investment",
    "expense": "Expense", "reminder": "Reminder", "task": "Task", "vault": "Vault item",
    "photo": "Photo", "gallery": "Gallery", "document": "Document", "album": "Album",
    "master": "List", "user": "User", "system": "System",
}

# Field names as people know them, not as the database spells them.
FIELD_LABELS = {
    "emi_amount": "EMI amount", "next_due_date": "next due date", "txn_date": "date",
    "due_date": "due date", "renewal_date": "renewal date", "maturity_date": "maturity date",
    "sum_assured": "sum assured", "invested_amount": "invested amount",
    "current_value": "current value", "policy_no": "policy number", "invest_type": "type",
    "loan_type": "type", "policy_type": "type", "card_name": "card name",
    "is_done": "done", "is_favorite": "favourite", "is_trashed": "trashed",
    "notify_push": "push reminder", "module_ref": "linked to", "last4": "last 4 digits",
    "credit_limit": "credit limit", "billing_day": "billing day", "due_day": "due day",
}


def _pretty_field(name: str) -> str:
    return FIELD_LABELS.get(name, name.replace("_", " "))


# Actions where the "user" the row points at IS the person who did it. Naming them
# again would read as "Signed in user Rahul" — the actor is already shown.
SELF_ACTIONS = {"login", "login_failed", "login_locked", "login_suspended",
                "password_change", "password_change_failed", "profile_update",
                "avatar_update", "push_subscribe"}

# Actions whose verb is already a complete sentence ("Created a user", "Signed in").
# Appending the entity noun to these produces "Created a user user".
SELF_TITLED = SELF_ACTIONS | {
    "user_create", "user_update", "user_delete", "permission_change",
    "purge_cdn", "export_bundle", "empty_trash", "dedupe", "reveal", "tag_person",
    "reindex",
}


def _name_of(uid, names: dict) -> str:
    """A person's name, or an honest placeholder. A deleted account still has to be
    identifiable — falling back to 'System' would attribute their actions to nobody."""
    if not uid:
        return "System"
    return names.get(uid) or f"User #{uid}"


def _row(log: AuditLog, names: dict, viewer_id: int) -> dict:
    try:
        meta = json.loads(log.meta) if log.meta else {}
    except ValueError:
        meta = {}
    verb, tone = ACTIONS.get(log.action, (log.action.replace("_", " ").capitalize(), "info"))
    diff = meta.get("changes") or {}

    label = meta.get("label")
    # Admin actions point at another account; show whose, not just the number. When
    # the account has since been deleted its name is gone from `users`, so fall back
    # to the address the action itself recorded before reaching for the bare id.
    if not label and log.entity == "user" and log.entity_id and log.action not in SELF_ACTIONS:
        label = names.get(log.entity_id) or meta.get("email") or f"User #{log.entity_id}"

    who = _name_of(log.user_id, names)
    # A failed sign-in has no authenticated user by definition; the address that was
    # tried is the only identity there is, and it's the one worth showing.
    if not log.user_id and meta.get("email"):
        who = str(meta["email"])[:190]
    mine = log.user_id == viewer_id

    # Composed here rather than in the app: only this side knows whether the verb
    # already names the thing it acted on.
    noun = ENTITY_LABELS.get(log.entity or "", (log.entity or "").capitalize())
    if log.action in SELF_TITLED:
        title = f"{verb} “{label}”" if label else verb
    elif label:
        title = f"{verb} {noun.lower()} “{label}”"
    elif noun:
        # No name recorded — older rows, and bulk actions that cover many records.
        # A raw row number tells the reader nothing, so name the kind of thing
        # instead. Passive reads correctly for every verb ("Photo moved to trash"),
        # where the active form would not.
        title = f"{noun} {verb.lower()}"
    else:
        title = verb

    return {
        "title": title,
        "id": log.id,
        "at": log.created_at.isoformat(timespec="seconds") if log.created_at else None,
        "action": log.action,
        "verb": verb,
        "tone": tone,
        "entity": log.entity,
        "entity_label": ENTITY_LABELS.get(log.entity or "", (log.entity or "").capitalize()),
        "entity_id": log.entity_id,
        "label": label,
        "by": who,
        "by_id": log.user_id,
        "mine": mine,
        "ip": log.ip,
        "security": log.action in SECURITY_ACTIONS,
        "changes": [{"field": _pretty_field(k), "from": v[0], "to": v[1]}
                    for k, v in diff.items()] if isinstance(diff, dict) else [],
        # Everything else the action recorded (photo counts, export paths, ...),
        # minus the two keys already surfaced above.
        "extra": {k: v for k, v in meta.items() if k not in ("label", "changes")},
    }


def _scoped(db: Session, user: User, who: int | None):
    """Base query honouring who may see what."""
    q = db.query(AuditLog)
    if user.role != "admin":
        return q.filter(AuditLog.user_id == user.id)
    if who:
        return q.filter(AuditLog.user_id == who)
    return q


@router.get("")
def index(offset: int = 0, limit: int = 50, kind: str = "all", action: str = "",
          entity: str = "", entity_id: int = 0, module: str = "", days: int = 0,
          user_id: int = 0, q: str = "",
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Paginated activity. `kind` is all | data | security.

    `entity` + `entity_id` narrow to one record — that's what the "History" link on
    a loan or a policy uses to show just that item's trail.
    """
    offset, limit = max(0, offset), min(max(1, limit), 200)
    if user_id and user.role != "admin":
        raise HTTPException(403, "Only an administrator can view another user's activity")

    sel = _scoped(db, user, user_id)
    if kind == "data":
        sel = sel.filter(AuditLog.action.notin_(SECURITY_ACTIONS))
    elif kind == "security":
        sel = sel.filter(AuditLog.action.in_(SECURITY_ACTIONS))
    if action:
        sel = sel.filter(AuditLog.action == action[:80])
    if module and module in MODULES:
        sel = sel.filter(AuditLog.entity.in_(MODULES[module][1]))
    if entity:
        sel = sel.filter(AuditLog.entity == entity[:60])
    if entity_id:
        sel = sel.filter(AuditLog.entity_id == entity_id)
    if days:
        sel = sel.filter(AuditLog.created_at >= ist.now() - timedelta(days=min(days, 3650)))
    term = (q or "").strip()[:80]
    if term:
        like = f"%{term}%"
        sel = sel.filter(AuditLog.meta.like(like) | AuditLog.action.like(like)
                         | AuditLog.entity.like(like))

    total = sel.count()
    rows = sel.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()

    # Two sets of names are needed: who did it, and — for admin actions — who it was
    # done to. Both resolved in one query rather than per row.
    names = {}
    ids = {r.user_id for r in rows if r.user_id}
    ids |= {r.entity_id for r in rows if r.entity == "user" and r.entity_id}
    if ids:
        names = {u.id: (u.name or u.email)
                 for u in db.query(User).filter(User.id.in_(ids)).all()}

    # Accounts that have since been deleted have no name left to look up. Their own
    # audit trail still carries the address, so recover it rather than showing a bare
    # number — the whole point of the log is that it outlives what it describes.
    missing = {i for i in ids if i not in names}
    if missing:
        for old in (db.query(AuditLog)
                    .filter(AuditLog.entity == "user", AuditLog.entity_id.in_(missing),
                            AuditLog.meta.like('%"email"%'))
                    .order_by(AuditLog.id.desc()).limit(300).all()):
            if old.entity_id in names:
                continue
            try:
                email = (json.loads(old.meta) or {}).get("email")
            except ValueError:
                email = None
            if email:
                names[old.entity_id] = str(email)[:190]

    return {
        "items": [_row(r, names, user.id) for r in rows],
        "total": total, "offset": offset, "limit": limit,
        "is_admin": user.role == "admin",
    }


@router.get("/summary")
def summary(days: int = 30, user_id: int = 0,
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Counts for the report header: how much happened, and of what kind."""
    if user_id and user.role != "admin":
        raise HTTPException(403, "Only an administrator can view another user's activity")
    days = min(max(1, days), 365)
    since = ist.now() - timedelta(days=days)

    sel = _scoped(db, user, user_id).filter(AuditLog.created_at >= since)
    by_action = dict(sel.with_entities(AuditLog.action, func.count(AuditLog.id))
                     .group_by(AuditLog.action).all())

    buckets = {"added": 0, "edited": 0, "deleted": 0, "security": 0}
    for act, n in by_action.items():
        if act in ("create", "upload", "scan"):
            buckets["added"] += n
        elif act in ("update", "done", "reopen", "restore", "tag_person",
                     "loan_paid", "card_paid"):
            buckets["edited"] += n
        elif act in ("delete", "trash", "empty_trash", "dedupe"):
            buckets["deleted"] += n
        if act in SECURITY_ACTIONS:
            buckets["security"] += n

    # Counts per module, so the picker can show how much is in each and hide the
    # parts of the app this user has never touched.
    by_entity = dict(_scoped(db, user, user_id)
                     .with_entities(AuditLog.entity, func.count(AuditLog.id))
                     .group_by(AuditLog.entity).all())
    module_counts: dict[str, int] = {}
    for ent, n in by_entity.items():
        key = ENTITY_TO_MODULE.get(ent or "")
        if key:
            module_counts[key] = module_counts.get(key, 0) + n
    modules = [{"key": k, "label": MODULES[k][0], "count": module_counts[k]}
               for k in MODULES if module_counts.get(k)]
    modules.sort(key=lambda m: -m["count"])

    top = sorted(((ACTIONS.get(a, (a, ""))[0], n) for a, n in by_action.items()),
                 key=lambda kv: -kv[1])[:6]
    first = _scoped(db, user, user_id).order_by(AuditLog.id.asc()).first()
    return {
        "days": days,
        "total": sum(by_action.values()),
        "buckets": buckets,
        "top": [{"label": label, "count": n} for label, n in top],
        "modules": modules,
        "tracking_since": first.created_at.date().isoformat()
        if first and first.created_at else ist.today().isoformat(),
    }
