"""Export ONE user's data into a standalone SQLite database.

This backs the "take my data to another computer" option every user gets. Unlike the
admin export it is a strict slice: only rows belonging to that user are copied, so
nothing of anyone else's travels with it.

Two deliberate decisions:

* The vault is re-encrypted under a freshly generated key. The server's shared vault
  key protects every user's secrets, so it must never leave this machine inside a
  personal copy.
* The user becomes the administrator of their own copy. On their own computer they
  are the only account, and they need to be able to manage it.
"""
import secrets
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from . import crypto
from . import models  # noqa: F401  — registers every table on Base.metadata
from .database import Base, engine as source

# Tables that carry a user_id and can be sliced directly.
DIRECT = [
    "user_modules", "loans", "loan_payments", "credit_cards", "card_payments",
    "insurance", "investments", "expenses", "reminders", "todos", "gallery_photos",
    "masters", "documents", "vault_items", "push_subscriptions", "notification_prefs",
    "people", "photo_faces", "albums",
]
# audit_logs is deliberately left behind: it is this server's security trail, it
# names other people's actions, and it means nothing on a fresh machine.
SKIP = {"audit_logs", "users"}

# Every module, so the user isn't locked out of part of their own copy.
ALL_MODULES = ["loans", "cards", "insurance", "investments", "expenses",
               "reminders", "todo", "habits", "vault", "gallery", "documents"]


def _reencrypt(rows: list[dict], new_key: str) -> int:
    """Move vault ciphertext onto the new key. Returns how many fields couldn't be
    read — those are already unreadable on this server and are copied untouched
    rather than silently dropped."""
    unreadable = 0
    for row in rows:
        for field in ("password_enc", "notes_enc"):
            blob = row.get(field)
            if not blob:
                continue
            try:
                row[field] = crypto.encrypt_with(new_key, crypto.decrypt(blob))
            except Exception:
                unreadable += 1
    return unreadable


def export_for_user(target: Path, user_id: int) -> dict:
    """Write a personal copy of the database. Returns a summary including the new
    vault key, which the caller must save alongside the data."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    for path in (target, target.with_name(target.name + "-wal"),
                 target.with_name(target.name + "-shm")):
        if path.exists():
            path.unlink()

    dest = create_engine(f"sqlite:///{target.as_posix()}")
    Base.metadata.create_all(bind=dest)
    tables = {t.name: t for t in Base.metadata.sorted_tables}

    new_key = secrets.token_hex(32)
    counts: dict[str, int] = {}
    unreadable = 0

    try:
        with source.connect() as src, dest.begin() as dst:
            users = tables["users"]
            row = src.execute(select(users).where(users.c.id == user_id)).mappings().first()
            if not row:
                raise LookupError(f"no such user: {user_id}")
            account = dict(row)
            account["role"] = "admin"      # sole owner of their own copy
            account["status"] = "active"
            dst.execute(insert(users), [account])
            counts["users"] = 1

            for name in DIRECT:
                table = tables[name]
                rows = [dict(r) for r in
                        src.execute(select(table).where(table.c.user_id == user_id)).mappings()]
                if name == "vault_items":
                    unreadable = _reencrypt(rows, new_key)
                if name == "user_modules":
                    # Their existing grants come across, then anything missing is
                    # added — a restricted account still gets a complete own copy.
                    have = {r["module_key"] for r in rows}
                    next_id = max((r["id"] for r in rows), default=0) + 1
                    for key in ALL_MODULES:
                        if key not in have:
                            rows.append({"id": next_id, "user_id": user_id, "module_key": key,
                                         "can_view": 1, "can_create": 1, "can_edit": 1,
                                         "can_delete": 1, "created_at": account.get("created_at"),
                                         "updated_at": account.get("updated_at")})
                            next_id += 1
                if rows:
                    dst.execute(insert(table), rows)
                counts[name] = len(rows)

            # Join tables have no user_id — scope them by the parents just copied.
            photo_ids = [r[0] for r in src.execute(
                select(tables["gallery_photos"].c.id)
                .where(tables["gallery_photos"].c.user_id == user_id))]
            album_ids = [r[0] for r in src.execute(
                select(tables["albums"].c.id)
                .where(tables["albums"].c.user_id == user_id))]

            for name, column, parents in (("photo_people", "photo_id", photo_ids),
                                          ("album_photos", "album_id", album_ids)):
                table = tables[name]
                rows = []
                if parents:
                    # Chunked: SQLite caps how many values one IN clause may hold.
                    for i in range(0, len(parents), 500):
                        rows += [dict(r) for r in src.execute(
                            select(table).where(table.c[column].in_(parents[i:i + 500]))
                        ).mappings()]
                if rows:
                    dst.execute(insert(table), rows)
                counts[name] = len(rows)
    finally:
        dest.dispose()

    return {
        "vault_key": new_key,
        "counts": {k: v for k, v in counts.items() if v},
        "rows": sum(counts.values()),
        "unreadable_vault_fields": unreadable,
        "photos": counts.get("gallery_photos", 0),
        "documents": counts.get("documents", 0),
    }
