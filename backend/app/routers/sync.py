"""What the phone needs to sync work it did while this computer was asleep.

The server here is somebody's home PC. It sleeps, it reboots, its tunnel drops —
and the phone in their pocket is then holding a record they just typed with
nowhere to put it. So the phone keeps it locally and replays it later, which
raises exactly one question this module exists to answer: **has this operation
already been accepted?**

It cannot be answered by looking at the records. A second expense for the same
amount on the same day is a perfectly ordinary thing for a person to enter
twice, so "does one like it exist?" is not the same question and gets it wrong
in both directions. The phone therefore mints a uuid per operation, and
`SyncOp` remembers the ones that have been honoured.

WHAT IS DELIBERATELY NOT HERE
Nothing writes records in this file. The record routers already own validation,
the writable-field allow-lists, RBAC and the audit trail, and a replay path that
reimplemented any of that would drift from the one the web app uses — which is
how two clients end up disagreeing about what a valid record is. This module
supplies the memory; the replay endpoint uses it.
"""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..models import SyncOp, User
from ..security import get_current_user

router = APIRouter(prefix="/api/sync", tags=["sync"])

#: How this protocol is versioned, as a whole number the phone compares.
#:
#: Bumped only when a phone written for the old number would get something
#: WRONG — not when something is added. An older phone must be able to read
#: this and decide it is safe to proceed.
PROTOCOL = 1

#: The modules a client may hold and replay.
#:
#: Vault is absent on purpose and must stay absent: its contents are decrypted
#: on this machine and the key never leaves it, so a phone holding a usable copy
#: would move every saved password onto the device most likely to be lost. See
#: CLAUDE.md §7.
#:
#: Gallery and documents are absent because they are large binaries with their
#: own transfer machinery, not because they are secret.
SYNCABLE = ("expenses", "loans", "cards", "insurance", "investments",
            "reminders", "todos", "notes", "habits")

OPS = ("create", "update", "delete")


def prior(db: Session, user_id: int, client_uuid: str) -> SyncOp | None:
    """The result this operation produced last time, if it has been seen.

    A hit means the phone is replaying something already honoured — almost
    always because the connection died between this server committing and the
    reply arriving. The phone cannot tell that from a request that never landed,
    so it retries, and without this the retry silently makes a second copy of a
    real record.
    """
    if not client_uuid:
        return None
    return (db.query(SyncOp)
            .filter(SyncOp.user_id == user_id,
                    SyncOp.client_uuid == client_uuid[:64])
            .first())


def remember(db: Session, user_id: int, client_uuid: str, module: str,
             op: str, server_id: int | None) -> None:
    """Record that this operation has been honoured.

    Committed by the CALLER, in the same transaction as the record it describes.
    Two commits would leave a window where the record exists and the memory of
    it does not, and a retry landing in that window is the very duplicate this
    is here to stop.
    """
    if not client_uuid:
        return
    db.add(SyncOp(user_id=user_id, client_uuid=client_uuid[:64],
                  module=module[:40], op=op[:10], server_id=server_id,
                  processed_at=ist.now()))


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """What this server can do, so a phone never has to assume.

    THIS ENDPOINT IS THE POINT OF THE EXERCISE. FastAPI drops a query parameter
    no argument claims and ignores a body field no model declares, both without
    complaint — so a phone built against a newer server does not fail against an
    older one, it succeeds incorrectly. That already happened here: `album_id`
    was added to the photo upload, the phone sent it, an older installation
    ignored it, and photos uploaded perfectly into an album that stayed empty
    with nothing anywhere saying why.

    A phone that cannot GET this (404) is talking to a server from before offline
    sync existed, and must say so and refuse rather than replay into it.

    Authenticated, because the module list says something about how this
    installation is configured and there is no reason to tell a stranger.
    """
    return {
        "protocol": PROTOCOL,
        "modules": list(SYNCABLE),
        "ops": list(OPS),
        # The phone stamps records locally against its own clock, which may be
        # minutes out. Handing back this machine's idea of now lets it show
        # "saved 10 minutes ago" honestly and, more importantly, lets it detect a
        # device clock so wrong that ordering would be nonsense.
        "server_time": ist.now().isoformat(),
        # Everything is bucketed by IST here (app/ist.py). A client that assumed
        # UTC would file an 11pm entry on the wrong day.
        "timezone": "Asia/Kolkata",
        "pending_known": db.query(SyncOp).filter(
            SyncOp.user_id == user.id).count(),
    }


# --------------------------------------------------------------------- replay

#: Module -> the RBAC key it guards on. NOT always the route name — todos guards
#: on "todo", singular — and getting one wrong here would hand somebody a module
#: the web app refuses them.
_GUARD = {
    "expenses": "expenses", "todos": "todo", "loans": "loans", "cards": "cards",
    "reminders": "reminders", "notes": "notes", "habits": "habits",
    "insurance": "insurance", "investments": "investments",
}


def _model(module: str):
    from ..models import (CreditCard, Expense, Habit, Insurance, Investment,
                          Loan, Note, Reminder, Todo)
    return {"expenses": Expense, "todos": Todo, "loans": Loan,
            "cards": CreditCard, "reminders": Reminder, "notes": Note,
            "habits": Habit, "insurance": Insurance,
            "investments": Investment}[module]


def _handlers(module: str):
    """The very functions the web app's own requests go through.

    Not a reimplementation. What counts as a valid record — the writable-field
    allow-lists, the required fields, the NOT NULL defaults, the audit rows —
    lives in those routers, and a replay path that restated any of it would
    drift until the phone and the browser disagreed about what can be saved.
    `resources._make()` hands back the same closures it registers, so the two
    generic modules come through the identical code.
    """
    from . import (cards, expenses, habits, loans, notes, reminders, resources,
                   todos)
    if module in resources.CONFIG:
        _, create, update, delete = resources._make(module)
        return create, update, delete
    mod = {"expenses": expenses, "todos": todos, "loans": loans, "cards": cards,
           "reminders": reminders, "notes": notes, "habits": habits}[module]
    return mod.create, mod.update, mod.delete


@router.post("/replay")
def replay(body: dict = Body(...), user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Apply what the phone did while this computer was unreachable.

    ONE OPERATION AT A TIME, EACH REPORTED SEPARATELY, and that shape is the
    point. A batch that succeeded or failed as a unit would let one refused
    record throw away nine good ones, and leave the phone with nothing to say
    beyond "sync failed". Every op comes back with its own outcome, so the phone
    can forget exactly the ones that landed and keep exactly the ones that did
    not — which is the difference between a queue and a data loss.
    """
    ops = body.get("ops") or []
    if not isinstance(ops, list):
        raise HTTPException(422, "ops must be a list")
    # Bounded so one call cannot run for minutes. The phone sends the rest in
    # the next round, and a progress bar that moves beats one long silence.
    results = []
    for raw in ops[:200]:
        if not isinstance(raw, dict):
            continue
        results.append(_one(
            db, user,
            str(raw.get("client_uuid") or "")[:64],
            str(raw.get("module") or ""),
            str(raw.get("op") or ""),
            raw))
    return {"results": results}


def _one(db: Session, user: User, uuid: str, module: str, op: str,
         raw: dict) -> dict:
    out = {"client_uuid": uuid, "module": module, "op": op}

    if module not in SYNCABLE or op not in OPS or not uuid:
        return {**out, "status": "rejected",
                "message": f"Cannot sync {op or 'that'} on {module or 'that'}"}

    seen = prior(db, user.id, uuid)
    if seen:
        # Already honoured. Nearly always a reply that never arrived rather than
        # the phone doing anything odd, so this is an ordinary outcome and not
        # an error — but it must not run the write a second time.
        return {**out, "status": "already", "server_id": seen.server_id}

    from ..security import guard
    try:
        action = {"create": "create", "update": "edit", "delete": "delete"}[op]
        guard(_GUARD[module], action)(user=user, db=db)
    except HTTPException as exc:
        return {**out, "status": "refused", "code": exc.status_code,
                "message": str(exc.detail)}

    create, update, delete = _handlers(module)
    target = raw.get("server_id")
    payload = raw.get("payload") or {}

    try:
        if op in ("update", "delete"):
            if not target:
                return {**out, "status": "rejected",
                        "message": "No record to change"}
            Model = _model(module)
            row = (db.query(Model)
                   .filter(Model.id == int(target), Model.user_id == user.id)
                   .first())
            if not row:
                # Gone on this side. Deleting something already deleted is the
                # outcome the phone was asking for, so it is not a failure.
                # Editing one is, and the owner has to hear about it rather than
                # watch the change quietly evaporate.
                if op == "delete":
                    remember(db, user.id, uuid, module, op, None)
                    db.commit()
                    return {**out, "status": "already", "server_id": None}
                return {**out, "status": "gone",
                        "message": "That record no longer exists here"}

            clash = _conflict(row, raw.get("base_updated_at"))
            if clash:
                return {**out, "status": "conflict", "server_id": int(target),
                        "server_updated_at": clash, "server_row": _safe(row),
                        "message": "Changed on the computer as well"}

        # The memory goes in BEFORE the handler, so the handler's own commit
        # carries the record and the record-of-it in one transaction. Written
        # after, a crash in between leaves a saved record with nothing saying it
        # was accepted — and the next replay makes a second one, which is the
        # entire failure this module exists to prevent.
        memo = SyncOp(user_id=user.id, client_uuid=uuid, module=module[:40],
                      op=op[:10], server_id=None, processed_at=ist.now())
        db.add(memo)

        if op == "create":
            res = create(body=payload, user=user, db=db)
            sid = ((res or {}).get("item") or {}).get("id")
        elif op == "update":
            update(id=int(target), body=payload, user=user, db=db)
            sid = int(target)
        else:
            delete(id=int(target), user=user, db=db)
            sid = None

        # A second commit, only to fill in what the row turned out to be. If
        # THIS one is lost the uuid is still recorded, so a replay answers
        # "already" with no id: the phone cannot learn the id, but it cannot
        # double-create either, and that is the right way round to fail.
        memo.server_id = sid
        db.commit()
        return {**out, "status": "ok", "server_id": sid}

    except HTTPException as exc:
        db.rollback()
        return {**out, "status": "refused", "code": exc.status_code,
                "message": str(exc.detail)}
    except Exception as exc:                       # pragma: no cover
        db.rollback()
        print(f"[sync] {module}.{op} failed: {exc}")
        return {**out, "status": "error",
                "message": "The computer could not save that"}


def _conflict(row, base) -> str | None:
    """Whether the record moved under the phone's feet while it was away.

    Returns the server's `updated_at` when it did, so the phone can show what it
    is up against rather than just refusing. A MISSING base counts as no
    conflict: an older phone that does not send one still has to be able to
    sync, and refusing every edit from it would be a worse failure than the rare
    overwrite it prevents.
    """
    have = getattr(row, "updated_at", None)
    if not base or have is None:
        return None
    theirs = str(have)[:19].replace(" ", "T")
    mine = str(base)[:19].replace(" ", "T")
    return theirs if theirs > mine else None


def _safe(row) -> dict:
    from ..helpers import to_dict
    try:
        return to_dict(row)
    except Exception:                              # pragma: no cover
        return {"id": getattr(row, "id", None)}
