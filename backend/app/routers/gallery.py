"""Photo gallery: upload (HEIC→JPEG + thumbnail), list, favourite, soft-trash,
automatic face clustering into people, person tagging, and "on this day" memories.
Face detection is best-effort via the external face service (port 8090)."""
import hashlib
import io
import json
import math
import os
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from .. import ist
from .. import dialect, indexer, places, storage, vision
from ..database import get_db
from ..helpers import audit
from ..models import Album, AlbumPhoto, GalleryPhoto, Person, PhotoFace, PhotoPerson, User
from ..security import guard
from ..signing import sign, verify

router = APIRouter(prefix="/api/gallery", tags=["gallery"])

THUMB_MAX = 480
# Below this cosine, CLIP is not really matching anything — showing the results
# anyway would fill the grid with the least-bad photos and read as broken.
SMART_MIN_SCORE = 0.20
MAX_SMART_HITS = 400
FACE_MATCH = 0.40  # SFace cosine: >=0.363 is same person; 0.40 is a safe margin
MAX_BYTES = 30 * 1024 * 1024  # 30 MB per photo


def photo_path(p: GalleryPhoto, variant: str) -> str:
    """Absolute path of one variant of a photo — derived from the OWNING ROW, never
    from anything in the request, so a path can't be steered by a caller."""
    return storage.media_path(storage.GALLERY, p.user_id, variant, p.filename)


def media_url(owner_id: int, variant: str, name: str) -> str:
    """Signed, expiring URL for one stored file. Photos are NOT served from a public
    static mount — every read goes through /media below with an owner-bound HMAC.
    The variant is inside the signed payload, so a thumb token can't fetch an original."""
    return f"/api/gallery/media/{variant}/{name}?t={sign(owner_id, f'{variant}/{name}')}"


def _present(p: GalleryPhoto) -> dict:
    return {
        "id": p.id,
        "url": media_url(p.user_id, storage.ORIGINAL, p.filename),
        "thumb_url": media_url(p.user_id, storage.THUMB, p.filename),
        "is_favourite": int(p.is_favorite or 0),
        "taken_at": p.taken_at.isoformat() if p.taken_at else None,
        "taken_fmt": p.taken_at.strftime("%d-%m-%Y") if p.taken_at else None,
        "caption": p.caption,
    }


def _detail(p: GalleryPhoto) -> dict:
    """Everything we know about one photo. Kept out of the grid payload — a 150-photo
    page doesn't need EXIF, and the viewer only ever asks about the one on screen."""
    d = _present(p)
    d.update({
        "orig_name": p.orig_name,
        "width": p.width or None,
        "height": p.height or None,
        "megapixels": round((p.width * p.height) / 1_000_000, 1) if p.width and p.height else None,
        "size_bytes": int(p.size_bytes or 0),
        "camera": p.camera or None,
        "lens": p.lens or None,
        "lat": float(p.lat) if p.lat is not None else None,
        "lon": float(p.lon) if p.lon is not None else None,
        "shot_at": p.shot_at.isoformat(timespec="seconds") if p.shot_at else None,
        "uploaded_at": p.created_at.isoformat(timespec="seconds") if p.created_at else None,
        "is_trashed": int(p.is_trashed or 0),
    })
    return d


def _cover_url(db: Session, photo_id: int | None) -> str | None:
    if not photo_id:
        return None
    p = db.query(GalleryPhoto).get(photo_id)
    return media_url(p.user_id, storage.THUMB, p.filename) if p else None


@router.get("/media/{variant}/{name}")
def media(variant: str, name: str, t: str = "", db: Session = Depends(get_db)):
    """Stream a stored photo. Authorised by the signed `t` token rather than a
    bearer header, because <img> tags cannot send one. The token binds the file
    to its owner, variant and expiry, so a leaked URL is neither permanent nor
    reusable for anyone else's photos."""
    if variant not in storage.VARIANTS or not storage.is_safe_name(name):
        raise HTTPException(404, "Not found")

    photo = db.query(GalleryPhoto).filter(GalleryPhoto.filename == name).first()
    if not photo or not verify(photo.user_id, f"{variant}/{name}", t):
        raise HTTPException(404, "Not found")

    path = photo_path(photo, variant)
    if not os.path.isfile(path):
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/jpeg", content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=3600"})


def _dhash(pil: Image.Image, size: int = 8) -> str:
    """Row difference-hash: 64 bits comparing adjacent grayscale pixels. Two visually
    similar images (resized, re-compressed, lightly edited) produce hashes a small
    Hamming distance apart, even when their bytes (and content_hash) differ."""
    img = pil.convert("L").resize((size + 1, size))
    px = list(img.getdata())
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return format(bits, "016x")


# EXIF tag numbers we care about. Using the raw ids avoids a name lookup table and
# matches what Pillow's getexif()/get_ifd() actually return.
_IFD_EXIF, _IFD_GPS = 0x8769, 0x8825
_T_MAKE, _T_MODEL, _T_DATETIME = 271, 272, 306
_T_ORIGINAL, _T_DIGITIZED = 36867, 36868
_T_EXPOSURE, _T_FNUMBER, _T_ISO, _T_FOCAL, _T_FOCAL35 = 33434, 33437, 34855, 37386, 41989


def _num(v) -> float | None:
    """Pillow hands back IFDRational (or occasionally a (num, den) tuple)."""
    try:
        if isinstance(v, (tuple, list)):
            return float(v[0]) / float(v[1]) if len(v) == 2 and float(v[1]) else float(v[0])
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None


def _gps_degrees(dms, ref) -> float | None:
    """(degrees, minutes, seconds) + N/S/E/W hemisphere -> signed decimal degrees."""
    try:
        d, m, s = (_num(x) for x in dms)
    except (TypeError, ValueError):
        return None
    if d is None or m is None or s is None:
        return None
    deg = d + m / 60 + s / 3600
    if str(ref).strip().upper().startswith(("S", "W")):
        deg = -deg
    return round(deg, 7)


def _read_exif(pil: Image.Image) -> dict:
    """Best-effort capture metadata for one image.

    Every field is optional and the whole thing is wrapped in try/except: phones strip
    EXIF when sharing, screenshots never had any, and a re-saved JPEG may carry a
    partial or malformed block. A photo with no metadata must still upload cleanly.
    """
    meta: dict = {"width": pil.width, "height": pil.height}
    try:
        ex = pil.getexif()
    except Exception:
        return meta
    if not ex:
        return meta

    try:
        make = str(ex.get(_T_MAKE) or "").strip().rstrip("\x00")
        model = str(ex.get(_T_MODEL) or "").strip().rstrip("\x00")
        # "Apple" + "Apple iPhone 15" would read as "Apple Apple iPhone 15".
        if model.lower().startswith(make.lower()) and make:
            make = ""
        camera = " ".join(x for x in (make, model) if x)
        if camera:
            meta["camera"] = camera[:120]
    except Exception:
        pass

    try:
        sub = ex.get_ifd(_IFD_EXIF) or {}
    except Exception:
        sub = {}

    raw = sub.get(_T_ORIGINAL) or sub.get(_T_DIGITIZED) or ex.get(_T_DATETIME)
    if raw:
        text = str(raw).strip()[:19]
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                meta["shot_at"] = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    # A single human-readable exposure line beats four sparsely-populated fields.
    bits: list[str] = []
    focal = _num(sub.get(_T_FOCAL35)) or _num(sub.get(_T_FOCAL))
    if focal:
        bits.append(f"{focal:.0f}mm")
    fnum = _num(sub.get(_T_FNUMBER))
    if fnum:
        bits.append(f"f/{fnum:g}")
    shutter = _num(sub.get(_T_EXPOSURE))
    if shutter:
        bits.append(f"1/{round(1 / shutter)}s" if shutter < 1 else f"{shutter:g}s")
    iso = sub.get(_T_ISO)
    iso = iso[0] if isinstance(iso, (tuple, list)) and iso else iso
    if _num(iso):
        bits.append(f"ISO {_num(iso):.0f}")
    if bits:
        meta["lens"] = " · ".join(bits)[:120]

    try:
        gps = ex.get_ifd(_IFD_GPS) or {}
        lat = _gps_degrees(gps.get(2), gps.get(1))
        lon = _gps_degrees(gps.get(4), gps.get(3))
        # (0, 0) is what a phone with the fix disabled writes — not the Gulf of Guinea.
        if lat is not None and lon is not None and (lat or lon):
            meta["lat"], meta["lon"] = lat, lon
    except Exception:
        pass

    return meta


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _search_filter(db: Session, uid: int, term: str):
    """Build the OR-clause for a free-text photo search.

    Matches the fields a person would actually type: what they named the photo, the
    camera, a person tagged in it, an album it belongs to, or a year/month. The
    person and album lookups resolve to id lists first — a JOIN would multiply rows
    and break the page count.
    """
    like = f"%{term}%"
    conds = [GalleryPhoto.caption.like(like),
             GalleryPhoto.orig_name.like(like),
             GalleryPhoto.camera.like(like),
             # Text read out of the picture itself — finds the photographed
             # receipt by its shop name, or the whiteboard by what was on it.
             GalleryPhoto.ocr_text.like(like)]

    ids: set[int] = set()
    ids.update(i for (i,) in db.query(PhotoPerson.photo_id)
               .join(Person, Person.id == PhotoPerson.person_id)
               .filter(Person.user_id == uid, Person.name.like(like)).all())
    ids.update(i for (i,) in db.query(AlbumPhoto.photo_id)
               .join(Album, Album.id == AlbumPhoto.album_id)
               .filter(Album.user_id == uid, Album.name.like(like)).all())
    if ids:
        conds.append(GalleryPhoto.id.in_(ids))

    low = term.lower()
    if term.isdigit() and len(term) == 4:
        conds.append(dialect.year_of(GalleryPhoto.taken_at) == int(term))
    elif len(low) >= 3 and low[:3] in _MONTHS:
        conds.append(dialect.month_of(GalleryPhoto.taken_at) == _MONTHS[low[:3]])
    return or_(*conds)


@router.get("")
def index(offset: int = 0, limit: int = 150, fav: int = 0, q: str = "", album: int = 0,
          smart: int = 0, kind: str = "", sort: str = "", near: str = "",
          user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    """Paginated gallery. Returns the requested page plus the true total count so
    the whole library (well beyond one page) is reachable via infinite scroll.
    `q` free-text searches, `album` narrows to one album, `fav` to favourites.

    `kind` and `sort` exist for the phone's Collections screen, which offers the
    same standing collections a photo app is expected to have:

        kind=screenshots   phone screenshots, by filename
        kind=located       photos that know where they were taken
        near=lat,lon[,km]  one place, as returned by /places
        sort=added         newest BACKED UP first, not newest taken

    `sort=added` is a genuinely different order from the default. A photo
    scanned in years later sorts by its EXIF date everywhere else in this app —
    deliberately, so it lands in the right month — which means "recently added"
    cannot be answered by the default ordering at all.
    """
    offset = max(0, offset)
    limit = min(max(1, limit), 300)  # clamp page size
    sel = db.query(GalleryPhoto).filter(GalleryPhoto.user_id == user.id,
                                        GalleryPhoto.is_trashed == 0)
    if fav:
        sel = sel.filter(GalleryPhoto.is_favorite == 1)

    near_centre = None
    k = (kind or "").strip().lower()
    if k == "screenshots":
        # By filename, because nothing else distinguishes one. Both platforms
        # name them predictably and neither records a flag we could read.
        sel = sel.filter(or_(GalleryPhoto.orig_name.ilike("%screenshot%"),
                             GalleryPhoto.orig_name.ilike("screen shot%"),
                             GalleryPhoto.caption.ilike("%screenshot%")))
    elif k == "located":
        sel = sel.filter(GalleryPhoto.lat.isnot(None),
                         GalleryPhoto.lon.isnot(None))

    if near:
        # "lat,lon" or "lat,lon,km" — one place from /places, opened.
        # A bounding box in SQL first, then the real distance in Python on what
        # survives: haversine cannot use an index, and narrowing 100k rows to a
        # few hundred by comparing two floats costs nothing.
        try:
            bits = [float(x) for x in near.split(",")]
            nlat, nlon = bits[0], bits[1]
            nkm = bits[2] if len(bits) > 2 else places.CLUSTER_KM
        except (ValueError, IndexError):
            raise HTTPException(422, "near must be lat,lon or lat,lon,km")
        dlat = nkm / 111.0
        # Longitude degrees shrink towards the poles. cos(89°) is near zero, so
        # clamp or the box becomes the whole world.
        dlon = nkm / max(1.0, 111.0 * math.cos(math.radians(nlat)))
        sel = sel.filter(GalleryPhoto.lat.between(nlat - dlat, nlat + dlat),
                         GalleryPhoto.lon.between(nlon - dlon, nlon + dlon))
        near_centre = (nlat, nlon, nkm)
    if album:
        # Ownership of the album is checked, not just its id — otherwise passing a
        # stranger's album id would confirm which of your photos they'd collected.
        if not db.query(Album).filter(Album.id == album, Album.user_id == user.id).first():
            raise HTTPException(404, "Album not found")
        sel = sel.filter(GalleryPhoto.id.in_(
            db.query(AlbumPhoto.photo_id).filter(AlbumPhoto.album_id == album)))
    term = (q or "").strip()[:80]

    # Search by what is IN the picture. Typed words are matched against the photos
    # themselves rather than their names, so "beach" finds a beach nobody labelled.
    # Ranked by similarity, which is the whole point — so date ordering is dropped.
    if term and smart and vision.clip_available():
        ranked = indexer.search(db, user.id, term, limit=MAX_SMART_HITS)
        ranked = [(pid, s) for pid, s in ranked if s >= SMART_MIN_SCORE]
        if ranked:
            allowed = {i for (i,) in sel.with_entities(GalleryPhoto.id).all()}
            ordered = [pid for pid, _ in ranked if pid in allowed]
            page = ordered[offset:offset + limit]
            found = {p.id: p for p in
                     db.query(GalleryPhoto).filter(GalleryPhoto.id.in_(page)).all()} if page else {}
            scores = dict(ranked)
            items = []
            for pid in page:
                p = found.get(pid)
                if p:
                    items.append({**_present(p), "score": round(scores.get(pid, 0), 4)})
            return {"items": items, "total": len(ordered), "offset": offset,
                    "limit": limit, "mode": "smart"}
        return {"items": [], "total": 0, "offset": offset, "limit": limit, "mode": "smart"}

    if term:
        sel = sel.filter(_search_filter(db, user.id, term))
    order = ([GalleryPhoto.created_at.desc(), GalleryPhoto.id.desc()]
             if (sort or "").strip().lower() == "added"
             else [GalleryPhoto.taken_at.desc(), GalleryPhoto.id.desc()])

    if near_centre:
        # The box over-selects at its corners, so page after the real distance
        # test, not before it — otherwise `total` counts photos the caller will
        # never be shown and the last page comes back short for no reason.
        nlat, nlon, nkm = near_centre
        keep = [p for p in sel.order_by(*order).all()
                if places.km_between(nlat, nlon, float(p.lat), float(p.lon)) <= nkm]
        total = len(keep)
        rows = keep[offset:offset + limit]
    else:
        total = sel.count()
        rows = sel.order_by(*order).offset(offset).limit(limit).all()
    return {"items": [_present(p) for p in rows], "total": total, "offset": offset,
            "limit": limit, "mode": "smart" if smart else "text"}


@router.get("/trash")
def trash_list(user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 1)
            .order_by(GalleryPhoto.updated_at.desc(), GalleryPhoto.id.desc()).limit(1000).all())
    return {"items": [_present(p) for p in rows]}


@router.get("/places")
def places_list(user: User = Depends(guard("gallery", "view")),
                db: Session = Depends(get_db)):
    """Where this library's photos were taken, grouped into places.

    Named offline from a table compiled into the app — see `places.py` for why
    a geocoding service is not an option here. Photos with no coordinates are
    simply absent; most libraries are mostly that, and an "Unknown" bucket
    holding four fifths of someone's photos is not a place.
    """
    rows = (db.query(GalleryPhoto.id, GalleryPhoto.lat, GalleryPhoto.lon)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 0,
                    GalleryPhoto.lat.isnot(None), GalleryPhoto.lon.isnot(None))
            .all())
    groups = places.cluster([(r.id, float(r.lat), float(r.lon)) for r in rows])
    out = []
    for g in groups:
        # One cover each. The ids come back in scan order, so ask the database
        # for the newest rather than taking whichever was seen first.
        cover = (db.query(GalleryPhoto)
                 .filter(GalleryPhoto.id.in_(g["ids"][:500]))
                 .order_by(GalleryPhoto.taken_at.desc(), GalleryPhoto.id.desc())
                 .first())
        item = {k: v for k, v in g.items() if k != "ids"}
        item["cover_url"] = _cover_url(db, cover.id) if cover else None
        out.append(item)
    return {"items": out, "total": len(out),
            "located": len(rows), "radius_km": places.CLUSTER_KM}


@router.get("/memories")
def memories(user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    """Photos taken on this day (month-day) in previous years, grouped by 'N years ago'."""
    today = ist.today()
    md = today.strftime("%m-%d")
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 0,
                    dialect.md(GalleryPhoto.taken_at) == md,
                    dialect.year_of(GalleryPhoto.taken_at) < today.year)
            .order_by(GalleryPhoto.taken_at.desc()).all())
    groups: dict[int, dict] = {}
    for r in rows:
        ya = today.year - r.taken_at.year
        g = groups.setdefault(ya, {"years": ya, "label": f"{ya} year{'s' if ya != 1 else ''} ago", "items": []})
        g["items"].append(_present(r))
    return {"groups": [groups[k] for k in sorted(groups)], "total": len(rows), "date": today.strftime("%d %b")}


# ---------- Photo indexing (faces + search-by-content) ----------


@router.get("/index")
def index_status(user: User = Depends(guard("gallery", "view"))):
    """Progress of the background pass, and whether the models are installed."""
    return indexer.status()


@router.post("/index")
def index_start(body: dict = Body(default={}),
                user: User = Depends(guard("gallery", "edit")),
                db: Session = Depends(get_db)):
    """Nudge the indexer. It also starts itself at boot and after an upload — this
    is for 'do it now', and for redoing the grouping from scratch."""
    # "ocr" belongs here too. Leaving it out of the allowed set meant the text
    # pass could only ever happen at boot: asking for it through this endpoint
    # silently dropped it, and "do it now" quietly did nothing.
    jobs = tuple(j for j in (body.get("jobs") or ["clip", "faces", "ocr"])
                 if j in ("faces", "clip", "ocr"))
    if not jobs:
        raise HTTPException(422, "Nothing to index")

    if body.get("rebuild"):
        # Throw away this user's grouping so it is rebuilt under the current rules.
        # Scoped to the caller: one gallery user must not be able to wipe another's.
        # Renamed people lose their names — the app warns before calling this.
        mine = db.query(GalleryPhoto.id).filter(GalleryPhoto.user_id == user.id)
        db.query(PhotoPerson).filter(PhotoPerson.photo_id.in_(mine)).delete(
            synchronize_session=False)
        db.query(PhotoFace).filter(PhotoFace.user_id == user.id).delete(
            synchronize_session=False)
        db.query(Person).filter(Person.user_id == user.id).delete(
            synchronize_session=False)
        db.commit()
        audit(db, user.id, "reindex", "gallery", None, {"label": "People rebuilt"})

    return indexer.start(jobs)


@router.post("/index/stop")
def index_stop(user: User = Depends(guard("gallery", "edit"))):
    indexer.stop()
    return {"stopping": True}


# ---------- Albums ----------


@router.get("/albums")
def albums_list(user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    """Every album with its live photo count and a cover thumbnail. Trashed photos
    are excluded from the count so an album doesn't claim items you can't see."""
    rows = (db.query(Album).filter(Album.user_id == user.id)
            .order_by(Album.name.asc()).all())
    if not rows:
        return {"albums": []}

    ids = [a.id for a in rows]
    counts = dict(db.query(AlbumPhoto.album_id, func.count(AlbumPhoto.photo_id))
                  .join(GalleryPhoto, GalleryPhoto.id == AlbumPhoto.photo_id)
                  .filter(AlbumPhoto.album_id.in_(ids), GalleryPhoto.is_trashed == 0)
                  .group_by(AlbumPhoto.album_id).all())
    # Fall back to the newest member when no cover was picked (or it was deleted).
    latest = dict(db.query(AlbumPhoto.album_id, func.max(AlbumPhoto.photo_id))
                  .join(GalleryPhoto, GalleryPhoto.id == AlbumPhoto.photo_id)
                  .filter(AlbumPhoto.album_id.in_(ids), GalleryPhoto.is_trashed == 0)
                  .group_by(AlbumPhoto.album_id).all())
    return {"albums": [{
        "id": a.id,
        "name": a.name,
        "count": int(counts.get(a.id, 0)),
        "cover_url": _cover_url(db, a.cover_id or latest.get(a.id)),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in rows]}


def _album_name(body: dict) -> str:
    name = str(body.get("name") or "").strip()[:120]
    if not name:
        raise HTTPException(422, "Album name is required")
    return name


def _own_album(db: Session, uid: int, aid: int) -> Album:
    a = db.query(Album).filter(Album.id == aid, Album.user_id == uid).first()
    if not a:
        raise HTTPException(404, "Album not found")
    return a


@router.get("/albums/suggested")
def albums_suggested(user: User = Depends(guard("gallery", "view")),
                     db: Session = Depends(get_db)):
    """Albums the library already implies, worked out from the photo vectors.

    Suggestions only — nothing is created until someone accepts one. A gallery
    that silently grows albums nobody asked for is a mess to undo.
    """
    from .. import albums_auto
    try:
        found = albums_auto.suggest(db, user.id)
    except Exception as exc:
        print(f"[albums] suggestion failed: {exc}")
        return {"suggestions": [], "error": "Could not group the photos"}
    existing = {n for (n,) in db.query(Album.name).filter(Album.user_id == user.id).all()}
    for s in found:
        s["exists"] = s["name"] in existing
        s["cover_url"] = None
        cover = db.query(GalleryPhoto).filter(GalleryPhoto.id == s["cover_id"]).first()
        if cover:
            s["cover_url"] = media_url(cover.user_id, storage.THUMB, cover.filename)
    return {"suggestions": found, "min_photos": albums_auto.MIN_ALBUM}


@router.post("/albums/suggested")
def album_accept(body: dict = Body(...), user: User = Depends(guard("gallery", "create")),
                 db: Session = Depends(get_db)):
    """Turn one suggestion into a real album."""
    name = (body.get("name") or "").strip()[:120]
    ids = [int(i) for i in (body.get("photo_ids") or [])][:2000]
    if not name or not ids:
        raise HTTPException(422, "A name and some photos are required")
    if db.query(Album).filter(Album.user_id == user.id, Album.name == name).first():
        raise HTTPException(409, "An album with that name already exists")

    owned = {p.id for p in db.query(GalleryPhoto.id)
             .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.id.in_(ids)).all()}
    if not owned:
        raise HTTPException(404, "Those photos are no longer here")
    now = ist.now()
    album = Album(user_id=user.id, name=name, cover_id=ids[0] if ids[0] in owned else None,
                  created_at=now, updated_at=now)
    db.add(album); db.flush()
    for pid in ids:
        if pid in owned:
            db.add(AlbumPhoto(album_id=album.id, photo_id=pid, created_at=now))
    db.commit()
    audit(db, user.id, "create", "album", album.id,
          {"label": name, "photos": len(owned), "source": "suggested"})
    return {"id": album.id, "name": name, "photos": len(owned)}


@router.post("/albums")
def album_create(body: dict = Body(...), user: User = Depends(guard("gallery", "create")),
                 db: Session = Depends(get_db)):
    name = _album_name(body)
    if db.query(Album).filter(Album.user_id == user.id, Album.name == name).first():
        raise HTTPException(409, "You already have an album with that name")
    now = ist.now()
    a = Album(user_id=user.id, name=name, created_at=now, updated_at=now)
    db.add(a); db.commit(); db.refresh(a)
    # Creating an album straight from a selection is the common case.
    added = _album_add(db, user.id, a, body.get("photo_ids") or [])
    audit(db, user.id, "create", "album", a.id, {"label": name, "photos": added})
    return {"id": a.id, "name": a.name, "count": added}


@router.get("/albums/{aid}")
def album_get(aid: int, user: User = Depends(guard("gallery", "view")),
              db: Session = Depends(get_db)):
    a = _own_album(db, user.id, aid)
    count = (db.query(func.count(AlbumPhoto.photo_id))
             .join(GalleryPhoto, GalleryPhoto.id == AlbumPhoto.photo_id)
             .filter(AlbumPhoto.album_id == aid, GalleryPhoto.is_trashed == 0).scalar())
    return {"id": a.id, "name": a.name, "count": int(count or 0)}


@router.put("/albums/{aid}")
def album_rename(aid: int, body: dict = Body(...),
                 user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    a = _own_album(db, user.id, aid)
    name = _album_name(body)
    clash = db.query(Album).filter(Album.user_id == user.id, Album.name == name,
                                   Album.id != aid).first()
    if clash:
        raise HTTPException(409, "You already have an album with that name")
    a.name = name
    if body.get("cover_id"):
        cid = int(body["cover_id"])
        if db.query(AlbumPhoto).filter(AlbumPhoto.album_id == aid,
                                       AlbumPhoto.photo_id == cid).first():
            a.cover_id = cid
    a.updated_at = ist.now()
    db.commit()
    return {"id": a.id, "name": a.name}


@router.delete("/albums/{aid}")
def album_delete(aid: int, user: User = Depends(guard("gallery", "delete")),
                 db: Session = Depends(get_db)):
    """Removes the album only. The photos stay in the gallery — an album is a view
    over them, so deleting one must never look like deleting the pictures."""
    a = _own_album(db, user.id, aid)
    label = a.name
    db.query(AlbumPhoto).filter(AlbumPhoto.album_id == aid).delete(synchronize_session=False)
    db.delete(a); db.commit()
    audit(db, user.id, "delete", "album", aid, {"label": label})
    return {"deleted": aid}


def _own_photo_ids(db: Session, uid: int, raw) -> list[int]:
    """Filter a caller-supplied id list down to photos this user actually owns."""
    ids = {int(x) for x in (raw or []) if str(x).lstrip("-").isdigit()}
    if not ids:
        return []
    return [i for (i,) in db.query(GalleryPhoto.id)
            .filter(GalleryPhoto.user_id == uid, GalleryPhoto.id.in_(ids)).all()]


def _album_add(db: Session, uid: int, album: Album, raw) -> int:
    ids = _own_photo_ids(db, uid, raw)
    if not ids:
        return 0
    have = {i for (i,) in db.query(AlbumPhoto.photo_id)
            .filter(AlbumPhoto.album_id == album.id, AlbumPhoto.photo_id.in_(ids)).all()}
    now = ist.now()
    added = 0
    for pid in ids:
        if pid in have:
            continue  # adding a photo twice is a no-op, not an error
        db.add(AlbumPhoto(album_id=album.id, photo_id=pid, created_at=now))
        added += 1
    if not album.cover_id and ids:
        album.cover_id = ids[0]
    album.updated_at = now
    db.commit()
    return added


@router.post("/albums/{aid}/photos")
def album_add_photos(aid: int, body: dict = Body(...),
                     user: User = Depends(guard("gallery", "edit")),
                     db: Session = Depends(get_db)):
    a = _own_album(db, user.id, aid)
    added = _album_add(db, user.id, a, body.get("photo_ids") or [])
    return {"album_id": aid, "added": added}


@router.post("/albums/{aid}/remove")
def album_remove_photos(aid: int, body: dict = Body(...),
                        user: User = Depends(guard("gallery", "edit")),
                        db: Session = Depends(get_db)):
    """Take photos out of an album. POST rather than DELETE because a DELETE with a
    request body is unevenly supported across proxies and fetch implementations."""
    a = _own_album(db, user.id, aid)
    ids = _own_photo_ids(db, user.id, body.get("photo_ids") or [])
    if not ids:
        return {"album_id": aid, "removed": 0}
    n = (db.query(AlbumPhoto)
         .filter(AlbumPhoto.album_id == aid, AlbumPhoto.photo_id.in_(ids))
         .delete(synchronize_session=False))
    if a.cover_id in ids:
        a.cover_id = None
    a.updated_at = ist.now()
    db.commit()
    return {"album_id": aid, "removed": int(n)}


# ---------- Single-photo detail ----------


def _backfill_dims(db: Session, p: GalleryPhoto) -> None:
    """Photos uploaded before the metadata columns existed have no size on record.
    Read it from the stored file the first time someone opens the info panel —
    cheap for one photo, and far better than a sweep over the whole library."""
    if p.width and p.height:
        return
    try:
        with Image.open(photo_path(p, storage.ORIGINAL)) as im:
            p.width, p.height = im.width, im.height
        if not p.size_bytes:
            p.size_bytes = os.path.getsize(photo_path(p, storage.ORIGINAL))
        db.commit()
    except Exception:
        db.rollback()


@router.get("/{id}/info")
def photo_info(id: int, user: User = Depends(guard("gallery", "view")),
               db: Session = Depends(get_db)):
    """Full metadata for one photo, plus who's tagged in it and which albums hold it."""
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id,
                                      GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    _backfill_dims(db, p)
    people = (db.query(Person).join(PhotoPerson, PhotoPerson.person_id == Person.id)
              .filter(PhotoPerson.photo_id == id, Person.user_id == user.id).all())
    albums = (db.query(Album).join(AlbumPhoto, AlbumPhoto.album_id == Album.id)
              .filter(AlbumPhoto.photo_id == id, Album.user_id == user.id).all())
    return {
        "photo": _detail(p),
        "people": [{"id": x.id, "name": x.name} for x in people],
        "albums": [{"id": x.id, "name": x.name} for x in albums],
    }


@router.get("/{id}/people")
def photo_people(id: int, user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    if not db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first():
        raise HTTPException(404, "Photo not found")
    rows = (db.query(Person).join(PhotoPerson, PhotoPerson.person_id == Person.id)
            .filter(PhotoPerson.photo_id == id, Person.user_id == user.id).all())
    return {"people": [{"id": p.id, "name": p.name} for p in rows]}


@router.post("/{id}/tag")
def tag(id: int, body: dict = Body(...), user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    if not db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first():
        raise HTTPException(404, "Photo not found")
    now = ist.now()
    person_id = int(body.get("person_id") or 0)

    if not person_id and (body.get("name") or "").strip():
        name = body["name"].strip()
        ex = db.query(Person).filter(Person.user_id == user.id, Person.name == name).first()
        if ex:
            person_id = ex.id
        else:
            p = Person(user_id=user.id, name=name, cover_id=id, created_at=now, updated_at=now)
            db.add(p); db.commit(); db.refresh(p)
            person_id = p.id
    if not person_id:
        raise HTTPException(422, "Provide a person name")

    person = db.query(Person).filter(Person.id == person_id, Person.user_id == user.id).first()
    if not person:
        raise HTTPException(404, "Person not found")
    if not db.query(PhotoPerson).filter(PhotoPerson.photo_id == id, PhotoPerson.person_id == person_id).count():
        db.add(PhotoPerson(photo_id=id, person_id=person_id, created_at=now))
    if not person.cover_id:
        person.cover_id = id
    db.commit()
    audit(db, user.id, "tag_person", "gallery", id, {"label": person.name, "person": person_id})
    return {"person_id": person_id, "name": person.name}


@router.post("/{id}/untag")
def untag(id: int, body: dict = Body(...), user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    # Ownership of BOTH sides is checked — otherwise any signed-in user could strip
    # tags from another user's photos just by guessing ids.
    if not db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first():
        raise HTTPException(404, "Photo not found")
    pid = int(body.get("person_id") or 0)
    if not db.query(Person).filter(Person.id == pid, Person.user_id == user.id).first():
        raise HTTPException(404, "Person not found")
    db.query(PhotoPerson).filter(PhotoPerson.photo_id == id, PhotoPerson.person_id == pid).delete()
    db.commit()
    return {"ok": True}


def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read an upload into memory, aborting as soon as it exceeds `limit` — so an
    oversized (or endless) body can't exhaust RAM before we get to check it.

    Reads the spooled file directly rather than awaiting UploadFile.read, because
    the endpoint below is deliberately synchronous. See the note on it."""
    chunks, total = [], 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"File too large (max {limit // (1024 * 1024)} MB)")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/upload")
def upload(file: UploadFile = File(...), faces: int = 1,
           user: User = Depends(guard("gallery", "create")),
           db: Session = Depends(get_db)):
    """Take one photo in, normalise it, store it, thumbnail it.

    SYNCHRONOUS ON PURPOSE — do not put `async` back on this.

    Everything below is blocking CPU work: decoding HEIC, two full-size JPEG
    encodes, a thumbnail and a perceptual hash, roughly three quarters of a second
    per 12MP photo. As `async def` that ran ON the event loop, which had two
    consequences, both measured rather than assumed:

      * uploads could not overlap. Sending four at once took 6.71s for eight
        photos against 6.02s one at a time — client concurrency was not merely
        useless, it was slightly negative, because four requests contending for
        one thread is worse than a queue. A phone backing up 500 photos was
        paying 0.75s each, in series, however clever the client was.
      * every OTHER request stalled behind it. /api/health, which touches nothing
        and should answer in about two milliseconds, had a median of 102ms and a
        worst case of 527ms while a single upload ran. The whole app froze —
        which is what "it doesn't feel like a native app" actually was.

    Declared `def`, FastAPI runs it in the threadpool instead, so photos are
    processed in parallel and the event loop stays free to serve everything else.
    Pillow releases the GIL for encode and decode, so this is real parallelism and
    not just tidier queueing.
    """
    raw = _read_capped(file, MAX_BYTES)
    return store_photo(db, user, raw, file.filename or "")


def store_photo(db: Session, user: User, raw: bytes, filename: str) -> dict:
    """Decode, de-duplicate, store and thumbnail one photo. The whole of upload.

    Shared with the device-token route so a photo arriving from an iPhone shortcut
    is the same photo as one dragged in here — same de-duplication, same EXIF, same
    thumbnail. Two copies of this would drift, and the half that drifted would be
    the one nobody is looking at.
    """
    if not raw:
        raise HTTPException(400, "Empty file")

    # Decode (pillow-heif handles HEIC/HEIF) and normalise to RGB JPEG bytes once,
    # so the content hash is stable regardless of the original container/encoding.
    try:
        pil = Image.open(io.BytesIO(raw))
        src_format = pil.format          # convert() clears it, and it decides the store below
        meta = _read_exif(pil)  # read before convert(), which drops the EXIF block
        exif_blob = pil.info.get("exif")
        pil = pil.convert("RGB") if pil.mode not in ("RGB", "L") else pil
        buf = io.BytesIO(); pil.save(buf, format="JPEG", quality=90)
        jpg = buf.getvalue()
    except Exception:
        raise HTTPException(400, "Unsupported image")

    # Hash the metadata-free encoding: EXIF differs between a photo and its shared
    # copy, so including it would make the exact-duplicate finder miss real pairs —
    # and it would disagree with the hashes backfilled from older stored files.
    content_hash = hashlib.sha256(jpg).hexdigest()

    # Idempotent upload: the same image (e.g. re-picked after an accidental refresh)
    # never creates a second row. A matching trashed photo is quietly restored.
    dup = (db.query(GalleryPhoto)
           .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.content_hash == content_hash)
           .first())
    if dup:
        if dup.is_trashed:
            dup.is_trashed = 0
            dup.updated_at = ist.now()
            db.commit()
        return {"item": _present(dup), "faces_found": 0, "duplicate": True}

    fname = f"{uuid.uuid4().hex}.jpg"
    # The file on disk keeps its EXIF so a download really is the user's original;
    # only the hashing copy above is stripped.
    #
    # When they sent us a JPEG, the original bytes ARE that file — so store them.
    # Re-encoding them was a second full-size encode per photo, the most expensive
    # thing on this path, spent on re-compressing an image at quality 90 that was
    # already compressed. It degraded the photo it was there to preserve, and on a
    # phone backup it doubled the cost of every single JPEG.
    #
    # HEIC still has to be encoded — there is no JPEG in the box to keep.
    if src_format == "JPEG":
        stored = raw
    elif exif_blob:
        try:
            ebuf = io.BytesIO(); pil.save(ebuf, format="JPEG", quality=90, exif=exif_blob)
            stored = ebuf.getvalue()
        except Exception:
            stored = jpg  # malformed EXIF — store the clean encoding instead
    else:
        stored = jpg
    storage.save(storage.GALLERY, user.id, storage.ORIGINAL, fname, stored)
    thumb = pil.copy(); thumb.thumbnail((THUMB_MAX, THUMB_MAX))
    tbuf = io.BytesIO(); thumb.save(tbuf, format="JPEG", quality=80)
    storage.save(storage.GALLERY, user.id, storage.THUMB, fname, tbuf.getvalue())

    orig_name = (filename or "")[-255:] or None
    caption = os.path.splitext(filename or "")[0] or None
    shot_at = meta.get("shot_at")
    now = ist.now()
    photo = GalleryPhoto(user_id=user.id, filename=fname, caption=caption,
                         # The date the photo was TAKEN drives sort order and Memories.
                         # Without EXIF the upload date is the only honest answer.
                         taken_at=shot_at.date() if shot_at else ist.today(),
                         is_favorite=0, is_trashed=0, size_bytes=len(raw),
                         content_hash=content_hash, phash=_dhash(pil),
                         orig_name=orig_name, width=meta.get("width"), height=meta.get("height"),
                         camera=meta.get("camera"), lens=meta.get("lens"),
                         lat=meta.get("lat"), lon=meta.get("lon"), shot_at=shot_at,
                         created_at=now, updated_at=now)
    db.add(photo); db.commit(); db.refresh(photo)

    # Face detection is NOT done here. It used to call an external service that was
    # never actually deployed, so every upload silently found nothing — which is why
    # People stayed empty. The background indexer now handles it, off the request
    # path, so a bulk upload of 500 photos isn't 500 model runs the user waits for.
    faces_found = 0
    audit(db, user.id, "upload", "photo", photo.id,
          {"label": photo.caption or photo.orig_name or f"Photo {photo.id}", "faces": faces_found})

    # Nudge the indexer so a new photo is grouped and made searchable without
    # anyone pressing anything. A no-op when a pass is already running, and the
    # running pass re-queries for work, so a 500-photo upload is picked up as it
    # arrives rather than starting 500 threads.
    #
    # note_upload() first: it tells that pass to stand aside while photos are
    # still landing. Indexing has no deadline; the person watching the upload bar
    # does. See indexer.UPLOAD_QUIET_SECONDS.
    try:
        indexer.note_upload()
        indexer.start()
    except Exception as exc:
        print(f"[gallery] could not nudge the indexer: {exc}")

    return {"item": _present(photo), "faces_found": faces_found, "duplicate": False}


def _backfill_hashes(db: Session, uid: int) -> None:
    """Compute content_hash for the user's photos that predate the column
    (or were uploaded before hashing) by hashing their stored full-size file."""
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == uid,
                    (GalleryPhoto.content_hash.is_(None)) | (GalleryPhoto.content_hash == ""))
            .all())
    changed = False
    for p in rows:
        try:
            with open(photo_path(p, storage.ORIGINAL), "rb") as fh:
                p.content_hash = hashlib.sha256(fh.read()).hexdigest()
                changed = True
        except (OSError, ValueError):
            continue
    if changed:
        db.commit()


@router.get("/duplicates")
def duplicates(user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    """Group live photos that share identical content. Suggests keeping the
    earliest of each group and trashing the rest."""
    _backfill_hashes(db, user.id)
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 0,
                    GalleryPhoto.content_hash.isnot(None), GalleryPhoto.content_hash != "")
            .order_by(GalleryPhoto.id.asc()).all())
    buckets: dict[str, list[GalleryPhoto]] = {}
    for p in rows:
        buckets.setdefault(p.content_hash, []).append(p)

    groups = []
    extra = 0
    for h, ps in buckets.items():
        if len(ps) < 2:
            continue
        keep = ps[0]  # earliest uploaded
        extra += len(ps) - 1
        groups.append({
            "hash": h,
            "count": len(ps),
            "keep_id": keep.id,
            "items": [_present(p) for p in ps],
        })
    # Biggest clusters first.
    groups.sort(key=lambda g: g["count"], reverse=True)
    return {"groups": groups, "group_count": len(groups), "extra": extra}


def _backfill_phash(db: Session, uid: int) -> None:
    """Compute the perceptual hash for live photos that don't have one yet
    (hashing the small thumbnail — fast). One-time per photo."""
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == uid, GalleryPhoto.is_trashed == 0,
                    (GalleryPhoto.phash.is_(None)) | (GalleryPhoto.phash == ""))
            .all())
    changed = False
    for p in rows:
        try:
            thumb = photo_path(p, storage.THUMB)  # small file — much faster to hash
            path = thumb if os.path.exists(thumb) else photo_path(p, storage.ORIGINAL)
            with Image.open(path) as im:
                p.phash = _dhash(im)
                changed = True
        except Exception:
            continue
    if changed:
        db.commit()


def _bands(total: int, nbands: int) -> list[tuple[int, int]]:
    """Partition `total` bits into `nbands` contiguous chunks → list of (shift, mask).
    With nbands = distance+1, any two hashes within `distance` bits share ≥1 identical
    band (pigeonhole), so bucketing by bands yields all candidate pairs, no misses."""
    out, shift, base, rem = [], 0, total // nbands, total % nbands
    for i in range(nbands):
        width = base + (1 if i < rem else 0)
        out.append((shift, (1 << width) - 1))
        shift += width
    return out


@router.get("/similar")
def similar(distance: int = 8, user: User = Depends(guard("gallery", "view")),
            db: Session = Depends(get_db)):
    """Group visually-similar (near-duplicate) live photos by perceptual-hash Hamming
    distance — catches resized/re-compressed/edited copies the exact finder misses."""
    _backfill_phash(db, user.id)
    distance = max(0, min(distance, 20))
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 0,
                    GalleryPhoto.phash.isnot(None), GalleryPhoto.phash != "")
            .order_by(GalleryPhoto.id.asc()).all())
    n = len(rows)
    hashes = [int(p.phash, 16) for p in rows]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Candidate generation via banding, then verify true Hamming distance.
    from collections import defaultdict
    for shift, mask in _bands(64, distance + 1):
        buckets: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            buckets[(hashes[i] >> shift) & mask].append(i)
        for idxs in buckets.values():
            if len(idxs) < 2:
                continue
            for a in range(len(idxs)):
                ia = idxs[a]
                for b in range(a + 1, len(idxs)):
                    ib = idxs[b]
                    if (hashes[ia] ^ hashes[ib]).bit_count() <= distance:
                        union(ia, ib)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    # Safety valve: a genuine near-duplicate set is small. An over-large component is
    # almost always transitive chaining (a→b→c where a and c aren't alike), so skip it
    # rather than propose trashing hundreds of unrelated photos.
    MAX_CLUSTER = 25
    groups, extra, skipped = [], 0, 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        if len(members) > MAX_CLUSTER:
            skipped += 1
            continue
        members.sort(key=lambda i: rows[i].id)  # earliest first
        extra += len(members) - 1
        groups.append({
            "hash": rows[members[0]].phash,
            "count": len(members),
            "keep_id": rows[members[0]].id,
            "items": [_present(rows[i]) for i in members],
        })
    groups.sort(key=lambda g: g["count"], reverse=True)
    return {"groups": groups, "group_count": len(groups), "extra": extra,
            "distance": distance, "skipped": skipped}


@router.post("/duplicates/resolve")
def resolve_duplicates(body: dict = Body(...),
                       user: User = Depends(guard("gallery", "delete")),
                       db: Session = Depends(get_db)):
    """Soft-trash the given photo ids (the duplicates the user chose to remove)."""
    ids = [int(x) for x in (body.get("delete_ids") or []) if str(x).isdigit()]
    if not ids:
        return {"trashed": 0}
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.id.in_(ids),
                    GalleryPhoto.is_trashed == 0).all())
    now = ist.now()
    for p in rows:
        p.is_trashed = 1
        p.updated_at = now
    db.commit()
    audit(db, user.id, "dedupe", "gallery", None, {"trashed": [p.id for p in rows]})
    return {"trashed": len(rows)}


@router.post("/{id}/favourite")
def favourite(id: int, user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    p.is_favorite = 0 if p.is_favorite else 1
    db.commit()
    return {"id": id, "is_favourite": int(p.is_favorite)}


@router.delete("/{id}")
def trash(id: int, user: User = Depends(guard("gallery", "delete")), db: Session = Depends(get_db)):
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    p.is_trashed = 1; db.commit()
    audit(db, user.id, "trash", "photo", id, {"label": p.caption or p.orig_name or f"Photo {p.id}"})
    return {"deleted": id}


@router.post("/{id}/restore")
def restore(id: int, user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    p.is_trashed = 0; db.commit()
    audit(db, user.id, "restore", "photo", id, {"label": p.caption or p.orig_name or f"Photo {p.id}"})
    return {"id": id, "restored": True}


@router.post("/trash/empty")
def empty_trash(user: User = Depends(guard("gallery", "delete")), db: Session = Depends(get_db)):
    """Permanently delete EVERY trashed photo for the user in one shot — removes each
    file and its face/person links, and nulls any person cover that pointed at them."""
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 1).all())
    ids = [p.id for p in rows]
    if not ids:
        return {"deleted": 0}
    for p in rows:
        for variant in storage.VARIANTS:
            storage.remove(storage.GALLERY, p.user_id, variant, p.filename)
    db.query(PhotoFace).filter(PhotoFace.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(PhotoPerson).filter(PhotoPerson.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(AlbumPhoto).filter(AlbumPhoto.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(Person).filter(Person.cover_id.in_(ids)).update({Person.cover_id: None}, synchronize_session=False)
    db.query(Album).filter(Album.cover_id.in_(ids)).update({Album.cover_id: None}, synchronize_session=False)
    db.query(GalleryPhoto).filter(GalleryPhoto.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    audit(db, user.id, "empty_trash", "gallery", None, {"deleted": len(ids)})
    return {"deleted": len(ids)}


@router.delete("/{id}/permanent")
def destroy(id: int, user: User = Depends(guard("gallery", "delete")), db: Session = Depends(get_db)):
    """Permanently delete a trashed photo — removes files and every face/person link."""
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    for variant in storage.VARIANTS:
        storage.remove(storage.GALLERY, p.user_id, variant, p.filename)
    db.query(PhotoFace).filter(PhotoFace.photo_id == id).delete()
    db.query(PhotoPerson).filter(PhotoPerson.photo_id == id).delete()
    db.query(AlbumPhoto).filter(AlbumPhoto.photo_id == id).delete()
    db.query(Person).filter(Person.cover_id == id).update({Person.cover_id: None})
    db.query(Album).filter(Album.cover_id == id).update({Album.cover_id: None})
    label = p.caption or p.orig_name or f"Photo {p.id}"
    db.delete(p); db.commit()
    audit(db, user.id, "delete", "photo", id, {"label": label})
    return {"deleted": id}
