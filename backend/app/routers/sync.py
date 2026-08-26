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
from fastapi import APIRouter, Depends
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
