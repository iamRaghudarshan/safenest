"""Life Memory — the things somebody has said into their phone.

THIS MODULE IS THE OTHER WAY ROUND FROM EVERY OTHER ONE HERE, and nearly every
decision below follows from that. Elsewhere a client creates a record and this
database owns it from that moment. A memory is SPOKEN, on a phone, very often
with no signal at all; it is written to the phone's own SQLite and shown
immediately, and it reaches this table whenever there is next a connection. So:

  * There is no create-on-the-server endpoint that invents a memory. Everything
    here arrives from a device, carrying the id it already has and the time it
    was actually said.
  * The push is IDEMPOTENT on `client_uuid`, minted on the phone. A reply that
    never arrives is the normal failure for this module, and a retry must return
    the original row rather than making a second one — a duplicate here cannot
    be told apart from a second thing somebody said.
  * `said_at` is when it was SAID. A sync after a week away would otherwise
    stamp seven days of memories with the same afternoon.
  * The words are never rewritten. Everything else about a memory is derived
    from them and can be rebuilt; the words cannot.

The facts are those the person CONFIRMED on the phone, and they are replaced
wholesale on an update rather than merged, because the phone is the authority on
what was agreed to and a merge would resurrect a tag somebody had removed.
"""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, to_dict
from ..models import Memory, MemoryFact, User
from ..security import guard

router = APIRouter(prefix="/api/memories", tags=["memories"])

#: The kinds the phone's reader can produce. Anything else is stored as 'thing'
#: rather than refused: a newer phone sending a kind this server has not heard of
#: must not lose the memory it belongs to, and a tag with the wrong colour is a
#: far smaller problem than a rejected push that retries for ever.
KINDS = {"expiry", "date", "amount", "place", "shop", "person", "thing"}

#: One push, and what a reasonable one holds. A person speaking into a phone for
#: a minute produces a paragraph, not a book, and the cap is here so a corrupt
#: body cannot land a megabyte in a Text column.
MAX_BODY = 8000
MAX_FACTS = 40


def _present(m: Memory, db: Session) -> dict:
    d = to_dict(m)
    d["spoken"] = bool(m.spoken)
    facts = (db.query(MemoryFact)
             .filter(MemoryFact.memory_id == m.id)
             .order_by(MemoryFact.id.asc()).all())
    d["facts"] = [
        {"kind": f.kind, "value": f.value, "at": ist.fmt(f.at)} for f in facts
    ]
    return d


def _set_facts(db: Session, memory: Memory, user: User, facts) -> None:
    """Replace a memory's facts with the ones the phone confirmed.

    Replaced, not merged. The phone is the authority on what the person agreed
    to, so a tag they removed there must not come back from here.
    """
    db.query(MemoryFact).filter(MemoryFact.memory_id == memory.id).delete()
    for f in (facts or [])[:MAX_FACTS]:
        if not isinstance(f, dict):
            continue
        value = str(f.get("value") or "").strip()[:500]
        if not value:
            continue
        kind = str(f.get("kind") or "thing").strip().lower()
        db.add(MemoryFact(
            memory_id=memory.id,
            user_id=user.id,
            kind=kind if kind in KINDS else "thing",
            value=value,
            at=f.get("at") or None,
        ))


@router.get("")
def list_memories(
    q: str = "",
    limit: int = 200,
    offset: int = 0,
    user: User = Depends(guard("memory")),
    db: Session = Depends(get_db),
):
    """Newest first, optionally matching `q` in the words or a confirmed fact.

    The words are searched as well as the tags, and the words matter more:
    somebody looking for "the blue suitcase" is remembering how they said it,
    not what anything labelled it.
    """
    query = db.query(Memory).filter(Memory.user_id == user.id)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        hits = [r[0] for r in db.query(MemoryFact.memory_id)
                .filter(MemoryFact.user_id == user.id,
                        MemoryFact.value.ilike(like)).all()]
        clause = Memory.body.ilike(like)
        if hits:
            clause = or_(clause, Memory.id.in_(hits))
        query = query.filter(clause)

    total = query.count()
    rows = (query.order_by(Memory.said_at.desc(), Memory.id.desc())
            .offset(max(0, offset)).limit(min(500, max(1, limit))).all())
    return {"items": [_present(m, db) for m in rows], "total": total}


@router.post("")
def push_memory(
    payload: dict = Body(...),
    user: User = Depends(guard("memory", "edit")),
    db: Session = Depends(get_db),
):
    """Take one memory from a phone.

    IDEMPOTENT ON client_uuid, which is the whole contract. The phone retries a
    push whose reply it never saw — that is the ordinary case for this module,
    not an exception — and a retry has to come back with the row the first
    attempt made.

    Returns `{"id": <server id>, "duplicate": bool}`. The phone stores that id
    and stops offering the memory; `duplicate` is there so a sync log can say
    what really happened rather than counting a replay as new.
    """
    uuid = str(payload.get("client_uuid") or "").strip()[:64]
    body = str(payload.get("body") or "").strip()[:MAX_BODY]
    if not uuid:
        # Refused rather than generated here. A server-minted key is useless: the
        # phone could not use it to recognise its own retry, which is the only
        # thing the key is for.
        raise HTTPException(400, "client_uuid is required")
    if not body:
        raise HTTPException(400, "a memory with no words is not a memory")

    existing = (db.query(Memory)
                .filter(Memory.user_id == user.id, Memory.client_uuid == uuid)
                .first())
    if existing:
        # A REPLAY. The facts are refreshed because the phone may have had the
        # chips edited since, but the words are left exactly as they arrived —
        # they are the one thing that cannot be rebuilt.
        _set_facts(db, existing, user, payload.get("facts"))
        existing.updated_at = ist.now()
        db.commit()
        return {"id": existing.id, "duplicate": True}

    memory = Memory(
        user_id=user.id,
        client_uuid=uuid,
        device_row_id=payload.get("device_row_id"),
        body=body,
        spoken=1 if payload.get("spoken") else 0,
        # When it was SAID, falling back to now only if the phone sent nothing —
        # which would be a bug there, and a memory stamped today is better than
        # one refused.
        said_at=payload.get("said_at") or ist.now(),
        photo_asset_id=str(payload.get("photo_asset_id") or "")[:120] or None,
        photo_id=payload.get("photo_id"),
        created_at=ist.now(),
        updated_at=ist.now(),
    )
    db.add(memory)
    db.flush()
    _set_facts(db, memory, user, payload.get("facts"))
    db.commit()
    db.refresh(memory)
    audit(db, user.id, "memory_create", "memory", memory.id)
    return {"id": memory.id, "duplicate": False}


@router.post("/batch")
def push_batch(
    payload: dict = Body(...),
    user: User = Depends(guard("memory", "edit")),
    db: Session = Depends(get_db),
):
    """Several at once, each answered separately.

    ONE REQUEST, NOT ONE PER MEMORY, because the phone catches up over a weak
    connection: a person who spoke forty times during a week with no signal
    should not need forty round trips, each able to fail on its own.

    PER-ITEM RESULTS, and that is the point of the shape. A batch that fails
    whole because the eleventh item was malformed would retry for ever and the
    other thirty-nine would never land. So each item reports its own id or its
    own error, and the phone marks exactly what got through.
    """
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items must be a non-empty list")
    if len(items) > 100:
        raise HTTPException(400, "send at most 100 at a time")

    out = []
    for item in items:
        if not isinstance(item, dict):
            out.append({"error": "not an object"})
            continue
        try:
            result = push_memory(payload=item, user=user, db=db)
            out.append({
                "client_uuid": item.get("client_uuid"),
                "device_row_id": item.get("device_row_id"),
                **result,
            })
        except HTTPException as exc:
            # Recorded against the item and moved past. The phone can then stop
            # retrying one it will never be able to send, instead of blocking the
            # queue behind it.
            db.rollback()
            out.append({
                "client_uuid": item.get("client_uuid"),
                "device_row_id": item.get("device_row_id"),
                "error": exc.detail,
            })
    return {"results": out}


@router.get("/coming")
def coming(
    user: User = Depends(guard("memory")),
    db: Session = Depends(get_db),
):
    """Confirmed dates still ahead, soonest first.

    The reason to keep dates on the server at all: a new phone has no alarms set,
    and this is what lets it be told what is coming instead of waiting for the
    person to say it again.
    """
    rows = (db.query(MemoryFact, Memory)
            .join(Memory, Memory.id == MemoryFact.memory_id)
            .filter(MemoryFact.user_id == user.id,
                    MemoryFact.at.isnot(None),
                    MemoryFact.at >= ist.now())
            .order_by(MemoryFact.at.asc()).limit(200).all())
    return {"items": [
        {
            "id": f.id,
            "memory_id": f.memory_id,
            "kind": f.kind,
            "value": f.value,
            "at": ist.fmt(f.at),
            "body": m.body,
        }
        for f, m in rows
    ]}


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: int,
    user: User = Depends(guard("memory", "delete")),
    db: Session = Depends(get_db),
):
    """Remove the server's copy.

    NO RECYCLE BIN HERE, unlike notes and documents, and it is deliberate: this
    row is a copy and the phone still holds the original, so a bin on this side
    would offer to restore something that was never the authority. Deleting on
    the phone is what deletes a memory.
    """
    memory = (db.query(Memory)
              .filter(Memory.id == memory_id, Memory.user_id == user.id)
              .first())
    if not memory:
        raise HTTPException(404, "No such memory")
    db.query(MemoryFact).filter(MemoryFact.memory_id == memory.id).delete()
    db.delete(memory)
    db.commit()
    audit(db, user.id, "memory_delete", "memory", memory_id)
    return {"ok": True}
