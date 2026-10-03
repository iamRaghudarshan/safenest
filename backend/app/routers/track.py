"""Track Me — where this household's phones have been.

THE MOST SENSITIVE TABLE IN THE PRODUCT, and it is worth being plain about
that at the top of the file. A location history says where somebody lives,
where they work, who they visit and when they are out. It is opt-in on the
phone, off until switched on, and it never leaves this machine.

Like Life Memory, THE PHONE HOLDS THE ORIGINAL. A fix is taken with the screen
off, often with no signal — in a basement, on a train, abroad — and if it is
not written to the phone's own database at that moment there is nothing to
write later. This table is a copy, pushed when there is a connection, keyed by
a uuid the phone minted so a retry cannot double a day.

THE TILE PROXY IS THE OTHER HALF, and it is not a convenience. The phone's
Places screen carries a long comment explaining why this app has never drawn a
map: the request for a tile IS the coordinate, so a map drawn from somebody
else's tiles posts a record of everywhere its owner has been to them. A
location timeline makes that very much worse than it was for photos. So the
phone asks THIS machine for tiles, and this machine fetches them from
OpenStreetMap once and keeps them. The phone never speaks to a tile host; this
computer does, and it already holds the whole history anyway.
"""
import os
import time

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist, storage
from ..database import get_db
from ..helpers import audit, to_dict
from ..models import TrackPlace, TrackPoint, User
from ..security import guard

router = APIRouter(prefix="/api/track", tags=["track"])

#: One push, capped. A phone catching up after a fortnight abroad has thousands
#: of fixes; it sends them in batches rather than one enormous transaction.
BATCH_MAX = 1000

#: Tiles are cached under the media root, which is already the one directory
#: this product treats as "large, disposable, and the user's own".
TILE_DIR = "track_tiles"

#: Where tiles come from, once. OpenStreetMap's policy asks for a real
#: User-Agent and discourages bulk downloading — a household browsing its own
#: timeline is neither, and caching means each tile is fetched at most once.
TILE_HOST = "https://tile.openstreetmap.org"
TILE_AGENT = "SafeNest/1.0 (self-hosted personal records; one household)"

#: Zoom levels worth serving. Below 3 the whole world is four tiles and above
#: 18 OSM asks you not to. Anything outside is refused rather than proxied, so
#: a hand-written URL cannot turn this into an open relay for somebody else's
#: bandwidth.
MIN_ZOOM, MAX_ZOOM = 3, 18


def _present(p: TrackPoint) -> dict:
    return to_dict(p)


@router.get("")
def list_points(
    day: str = "",
    limit: int = 2000,
    user: User = Depends(guard("track")),
    db: Session = Depends(get_db),
):
    """Fixes for one day, oldest first.

    `day` is a local date as the phone reckons it, `YYYY-MM-DD`. The phone does
    the cutting into stays and journeys itself — the rules for that will change,
    and a server that had already decided would leave every past day stuck with
    the rules of the week it was recorded.
    """
    q = db.query(TrackPoint).filter(TrackPoint.user_id == user.id)
    d = (day or "").strip()
    if d:
        # Inclusive of the whole local day. Compared as text because the column
        # holds ISO strings and both dialects order those correctly.
        q = q.filter(TrackPoint.at >= f"{d} 00:00:00",
                     TrackPoint.at <= f"{d} 23:59:59")
    rows = (q.order_by(TrackPoint.at.asc())
            .limit(min(10000, max(1, limit))).all())
    return {"items": [_present(p) for p in rows], "count": len(rows)}


@router.get("/days")
def days(
    user: User = Depends(guard("track")),
    db: Session = Depends(get_db),
):
    """Which days have anything, newest first, with a count each.

    So the phone can show a calendar of what it holds without fetching a year
    of coordinates to find out.
    """
    # The expressions themselves, not their labels. SQLAlchemy 2 refuses a bare
    # string in ORDER BY — "Textual SQL expression should be explicitly declared
    # as text()" — and it is right to: a label that happens to match a column
    # name would silently order by the wrong thing.
    dayof = func.substr(TrackPoint.at, 1, 10)
    rows = (db.query(dayof.label("d"), func.count(TrackPoint.id).label("n"))
            .filter(TrackPoint.user_id == user.id)
            .group_by(dayof).order_by(dayof.desc()).limit(400).all())
    return {"items": [{"day": r.d, "fixes": r.n} for r in rows]}


@router.post("/batch")
def push(
    payload: dict = Body(...),
    user: User = Depends(guard("track", "edit")),
    db: Session = Depends(get_db),
):
    """Take a batch of fixes from a phone.

    IDEMPOTENT ON client_uuid, per fix. A phone retrying a push whose reply it
    never saw is the ordinary case here, not an edge — this is a module that
    runs all day with the screen off — and a duplicated fix is not merely
    untidy: two fixes a second apart at the same spot look like a stop, and a
    day full of them re-cuts into places that were never places.

    Per-item results, so one malformed fix cannot block the thousands behind it.
    """
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items must be a non-empty list")
    if len(items) > BATCH_MAX:
        raise HTTPException(400, f"send at most {BATCH_MAX} at a time")

    uuids = [str(i.get("client_uuid") or "")[:64]
             for i in items if isinstance(i, dict)]
    known = {
        p.client_uuid: p.id
        for p in db.query(TrackPoint)
        .filter(TrackPoint.user_id == user.id,
                TrackPoint.client_uuid.in_([u for u in uuids if u])).all()
    }

    out = []
    made = 0
    for item in items:
        if not isinstance(item, dict):
            out.append({"error": "not an object"})
            continue
        uuid = str(item.get("client_uuid") or "")[:64]
        row_id = item.get("device_row_id")
        if not uuid:
            out.append({"device_row_id": row_id,
                        "error": "client_uuid is required"})
            continue
        if uuid in known:
            out.append({"device_row_id": row_id, "id": known[uuid],
                        "duplicate": True})
            continue
        try:
            lat = float(item.get("lat"))
            lon = float(item.get("lon"))
        except (TypeError, ValueError):
            out.append({"device_row_id": row_id, "error": "lat/lon required"})
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            out.append({"device_row_id": row_id,
                        "error": "that is not a place on Earth"})
            continue

        point = TrackPoint(
            user_id=user.id,
            client_uuid=uuid,
            device_row_id=row_id if isinstance(row_id, int) else None,
            # WHEN THE FIX WAS TAKEN, not when it arrived. A week abroad pushed
            # on landing would otherwise collapse onto one afternoon, which is
            # the whole history useless.
            at=item.get("at") or ist.now(),
            lat=lat,
            lon=lon,
            accuracy=float(item.get("accuracy") or 0),
            speed=item.get("speed"),
            battery=item.get("battery"),
            created_at=ist.now(),
        )
        db.add(point)
        db.flush()
        known[uuid] = point.id
        made += 1
        out.append({"device_row_id": row_id, "id": point.id,
                    "duplicate": False})

    db.commit()
    if made:
        audit(db, user.id, "track_push", "track", None)
    return {"results": out, "created": made}


@router.delete("")
def forget(
    day: str = "",
    user: User = Depends(guard("track", "delete")),
    db: Session = Depends(get_db),
):
    """Forget a day, or everything.

    NO RECYCLE BIN, and that is deliberate for this table alone. Everywhere else
    in this product a deleted thing goes to a bin because people delete things
    by accident. A location history is the one thing somebody may want gone in a
    hurry and gone completely, and a bin that quietly kept it would be the
    opposite of what the button says.
    """
    q = db.query(TrackPoint).filter(TrackPoint.user_id == user.id)
    d = (day or "").strip()
    if d:
        q = q.filter(TrackPoint.at >= f"{d} 00:00:00",
                     TrackPoint.at <= f"{d} 23:59:59")
    n = q.delete(synchronize_session=False)
    db.commit()
    audit(db, user.id, "track_forget", "track", None,
          {"day": d or "everything", "fixes": n})
    return {"forgotten": n}


# ------------------------------------------------------- places with names

@router.get("/places")
def list_places(
    user: User = Depends(guard("track")),
    db: Session = Depends(get_db),
):
    rows = (db.query(TrackPlace).filter(TrackPlace.user_id == user.id)
            .order_by(TrackPlace.name.asc()).all())
    return {"items": [to_dict(p) for p in rows]}


@router.post("/places")
def name_place(
    payload: dict = Body(...),
    user: User = Depends(guard("track", "edit")),
    db: Session = Depends(get_db),
):
    """Give somewhere a name.

    Named by the person, never looked up. See the module docstring: a
    reverse-geocode would send the coordinates of a house to a stranger's server
    to be told what its owner already knows.
    """
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "a place needs a name")
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        raise HTTPException(400, "lat and lon are required")

    place = TrackPlace(
        user_id=user.id, name=name, lat=lat, lon=lon,
        radius=float(payload.get("radius") or 150),
        created_at=ist.now())
    db.add(place)
    db.commit()
    db.refresh(place)
    return {"item": to_dict(place)}


@router.delete("/places/{place_id}")
def forget_place(
    place_id: int,
    user: User = Depends(guard("track", "delete")),
    db: Session = Depends(get_db),
):
    place = (db.query(TrackPlace)
             .filter(TrackPlace.id == place_id,
                     TrackPlace.user_id == user.id).first())
    if not place:
        raise HTTPException(404, "No such place")
    db.delete(place)
    db.commit()
    return {"ok": True}


# -------------------------------------------------------------- the tiles

@router.get("/tile/{z}/{x}/{y}.png")
def tile(
    z: int,
    x: int,
    y: int,
    user: User = Depends(guard("track")),
    db: Session = Depends(get_db),
):
    """One map tile, fetched once and then served from this machine.

    THIS IS WHY THE MAP IS ALLOWED TO EXIST. places_screen.dart on the phone
    carries the rule: the request for a tile IS the coordinate, so a map drawn
    from a public tile host posts a record of everywhere its owner has been to
    that host. Here the phone asks its own computer, and its own computer asks
    OpenStreetMap once per tile, for ever. OSM sees this machine's address
    fetching the area around a home it cannot connect to a person, and it sees
    each tile once rather than on every pan.

    Signed in and guarded like everything else: the tile cache is not a public
    mount, because the SET of tiles somebody has fetched is itself a map of
    where they have been.
    """
    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        raise HTTPException(400, "zoom out of range")
    span = 1 << z
    if not (0 <= x < span and 0 <= y < span):
        raise HTTPException(400, "that tile is not on the map")

    # Its own folder under the private root, NOT under a user's. A tile is the
    # same tile for everybody in the household, and filing it per user would
    # fetch the same picture of the same street once per person.
    folder = os.path.join(storage.PRIVATE_ROOT, TILE_DIR, str(z), str(x))
    path = os.path.join(folder, f"{y}.png")

    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), media_type="image/png",
                            headers={"Cache-Control": "public, max-age=2592000"})

    try:
        r = requests.get(f"{TILE_HOST}/{z}/{x}/{y}.png", timeout=20,
                         headers={"User-Agent": TILE_AGENT})
    except Exception as exc:
        print(f"[track] tile fetch failed: {exc}")
        raise HTTPException(503, "Could not fetch that part of the map")

    if r.status_code != 200 or not r.content:
        raise HTTPException(502, "The map service did not answer")

    try:
        os.makedirs(folder, exist_ok=True)
        # Written to a temporary name and moved, so a half-written tile is never
        # served: two phones asking for the same tile at once is ordinary, and a
        # truncated PNG caches the failure for ever.
        tmp = f"{path}.{int(time.time() * 1000)}.part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, path)
    except Exception as exc:
        # Serve it anyway. A cache that cannot be written is a slow map, not a
        # broken one.
        print(f"[track] could not cache tile: {exc}")

    return Response(r.content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=2592000"})
