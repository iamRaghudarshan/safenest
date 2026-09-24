"""Photo gallery: upload (HEIC→JPEG + thumbnail), list, favourite, soft-trash,
automatic face clustering into people, person tagging, and "on this day" memories.
Face detection is best-effort via the external face service (port 8090)."""
import hashlib
import io
import json
import math
import mimetypes
import os
import time
import uuid
from datetime import date, datetime, timedelta

from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from .. import ist
from .. import bursts as _bursts
from .. import dialect, indexer, nlquery, places, smartalbum, storage, vision
from ..database import get_db
from ..helpers import audit
from ..models import (Album, AlbumPhoto, GalleryPhoto, Person, PhotoFace,
                      PhotoLabel, PhotoPerson, PhotoVector, User)
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
# Videos are far bigger than photos — 30 MB rejected almost every real phone clip
# (a 413 the backup reported as "larger than your computer will accept"), so a
# "back up my phone" app silently kept none of them. This covers most phone clips.
# Kept to a size the in-memory read can hold without exhausting RAM across a few
# concurrent uploads; a truly huge 4K clip beyond this still needs a future
# stream-to-disk path rather than a bigger buffer.
MAX_VIDEO_BYTES = 256 * 1024 * 1024  # 256 MB per video


def thumb_name(p: GalleryPhoto) -> str:
    """The file that IS this item's thumbnail.

    For a photo it is the photo. For a video it is the poster frame, which is a
    JPEG saved under the same stem — a `.mp4` in the thumbnail folder would be
    a video the grid tried to draw as an image.
    """
    return (os.path.splitext(p.filename)[0] + ".jpg"
            if (p.kind or "photo") == "video" else p.filename)


_VIDEO_PLACEHOLDER: bytes | None = None


def _video_placeholder() -> bytes:
    """A neutral dark tile served when a video has no poster frame.

    OpenCV cannot decode some containers (iPhone HEVC .mov most often), so the
    poster JPEG is never written and the thumbnail request would 404 and render as
    a broken image. A plain dark tile under the client's play badge reads as a video
    still, not a fault. Built once and reused."""
    global _VIDEO_PLACEHOLDER
    if _VIDEO_PLACEHOLDER is None:
        buf = io.BytesIO()
        Image.new("RGB", (480, 480), (34, 36, 44)).save(buf, "JPEG", quality=70)
        _VIDEO_PLACEHOLDER = buf.getvalue()
    return _VIDEO_PLACEHOLDER


def photo_path(p: GalleryPhoto, variant: str) -> str:
    """Absolute path of one variant of a photo — derived from the OWNING ROW, never
    from anything in the request, so a path can't be steered by a caller."""
    name = thumb_name(p) if variant == storage.THUMB else p.filename
    return storage.media_path(storage.GALLERY, p.user_id, variant, name)


def media_url(owner_id: int, variant: str, name: str) -> str:
    """Signed, expiring URL for one stored file. Photos are NOT served from a public
    static mount — every read goes through /media below with an owner-bound HMAC.
    The variant is inside the signed payload, so a thumb token can't fetch an original."""
    return f"/api/gallery/media/{variant}/{name}?t={sign(owner_id, f'{variant}/{name}')}"


def _present(p: GalleryPhoto) -> dict:
    kind = (p.kind or "photo")
    return {
        "id": p.id,
        "url": media_url(p.user_id, storage.ORIGINAL, p.filename),
        "thumb_url": media_url(p.user_id, storage.THUMB, thumb_name(p)),
        "is_favourite": int(p.is_favorite or 0),
        "taken_at": p.taken_at.isoformat() if p.taken_at else None,
        "taken_fmt": p.taken_at.strftime("%d-%m-%Y") if p.taken_at else None,
        "caption": p.caption,
        "kind": kind,
        "duration_ms": p.duration_ms,
        # Dimensions ride along with the grid payload, unlike the rest of the
        # EXIF in _detail(). A justified timeline has to know each tile's shape
        # BEFORE it can lay a row out, and asking per photo would be a request
        # per tile. Two integers already on the row cost nothing to send.
        "width": p.width or None,
        "height": p.height or None,
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
    # thumb_name, not p.filename: an album or place whose cover is a video
    # would otherwise point at the .mp4 in the thumbnail folder and show
    # nothing at all.
    return media_url(p.user_id, storage.THUMB, thumb_name(p)) if p else None


@router.get("/media/{variant}/{name}")
def media(variant: str, name: str, t: str = "", db: Session = Depends(get_db)):
    """Stream a stored photo. Authorised by the signed `t` token rather than a
    bearer header, because <img> tags cannot send one. The token binds the file
    to its owner, variant and expiry, so a leaked URL is neither permanent nor
    reusable for anyone else's photos."""
    if variant not in storage.VARIANTS or not storage.is_safe_name(name):
        raise HTTPException(404, "Not found")

    photo = db.query(GalleryPhoto).filter(GalleryPhoto.filename == name).first()
    if photo is None and variant == storage.THUMB:
        # A video's poster is `<stem>.jpg` while its row says `<stem>.mp4`, so
        # an exact filename match finds nothing and every video came back as a
        # broken tile. Matched on the stem, and only for a thumbnail request —
        # the original still has to be asked for by its real name.
        stem = os.path.splitext(name)[0]
        if os.path.splitext(name)[1].lower() == ".jpg" and stem:
            photo = (db.query(GalleryPhoto)
                     .filter(GalleryPhoto.kind == "video",
                             GalleryPhoto.filename.like(f"{stem}.%"))
                     .first())
    if not photo or not verify(photo.user_id, f"{variant}/{name}", t):
        raise HTTPException(404, "Not found")

    path = photo_path(photo, variant)
    if not os.path.isfile(path):
        # A video whose poster frame could not be decoded (iPhone HEVC that the
        # shipped OpenCV can't read is the common case) has no <stem>.jpg on disk.
        # Serve a neutral dark tile rather than 404, so the grid shows a clean
        # placeholder under the play button instead of a broken image.
        if variant == storage.THUMB and (photo.kind or "") == "video":
            return Response(_video_placeholder(), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})
        raise HTTPException(404, "Not found")
    # The content type must match the bytes. Thumbnails are always JPEG posters
    # (a video's poster frame included). But the ORIGINAL of a video is an .mp4 /
    # .mov, and serving it as image/jpeg — which this did for every file — makes a
    # player refuse it with "could not be played". Derive it from the real file.
    if variant == storage.THUMB:
        ctype = "image/jpeg"
    else:
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=ctype, content_disposition_type="inline",
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
          bursts: int = 0,
          smart: int = 0, kind: str = "", sort: str = "", near: str = "", person: int = 0,
          archived: int = 0,
          label: str = "",
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
        sort=oldest        oldest TAKEN first (reverse of the default)
        person=<id>        every photo one person appears in

    `person` exists so the gallery can filter by a FACE rather than a name.
    Text search already matches a person's name, which is no use at all for the
    faces the clustering has found and nobody has named — and those are most of
    them. Tapping a face is the only way to ask about someone called "Person 7",
    and it composes with the date grouping, the media filters and the selection
    bar, which /api/people/{id}/photos does not.

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
    if k in ("video", "videos"):
        sel = sel.filter(GalleryPhoto.kind == "video")
    elif k in ("photo", "photos"):
        # `!= 'video'` rather than `== 'photo'`, so rows written before the
        # column existed — which the migration defaults, but a hand-edited or
        # restored database might not — still count as photos.
        sel = sel.filter(or_(GalleryPhoto.kind.is_(None),
                             GalleryPhoto.kind != "video"))
    elif k == "screenshots":
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
    # A smart album is a saved rule, not a list. Resolving it here means the
    # album answers itself every time it is opened, so a photo taken today
    # that matches appears without anybody filing it.
    if album:
        _al = (db.query(Album)
               .filter(Album.id == album, Album.user_id == user.id).first())
        if _al is not None and _al.rule:
            sel = smartalbum.apply(sel, db, user.id, smartalbum.parse(_al.rule))
            album = 0          # do not also join album_photos: it is empty

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

    # A sentence, split into the filters that already exist.
    #
    # "Alice at the beach in 2024" is a person, a label and a year, and until
    # now all three were handed to CLIP as one string — which finds beaches and
    # ignores Alice entirely. Anything the parser cannot place stays in the
    # text and still goes to semantic search, so a query with nothing
    # structured in it behaves exactly as it did before.
    if term and not person and not label:
        people_rows = db.query(Person.id, Person.name).filter(
            Person.user_id == user.id).all()
        parsed = nlquery.parse(term, [(p_.id, p_.name) for p_ in people_rows],
                               list(vision.LABELS))
        if parsed["matched"]:
            person = parsed["person_id"] or person
            label = parsed["label"] or label
            if parsed["kind"] and not kind:
                kind = parsed["kind"]
            if parsed["year"]:
                sel = sel.filter(dialect.year_of(GalleryPhoto.taken_at)
                                 == int(parsed["year"]))
            if parsed["month"]:
                sel = sel.filter(dialect.month_of(GalleryPhoto.taken_at)
                                 == int(parsed["month"]))
            # Whatever is left is the real search term. Empty means the whole
            # query was filters, and the filters ARE the answer.
            term = parsed["text"]

    if term:
        sel = sel.filter(_search_filter(db, user.id, term))

    # Browse by category. Kept separate from `q` on purpose: a label is an
    # exact bucket the indexer assigned, while q is a text search, and mixing
    # them would make "beach" mean two different things.
    if label:
        sel = sel.filter(GalleryPhoto.id.in_(
            db.query(PhotoLabel.photo_id).filter(
                PhotoLabel.user_id == user.id,
                PhotoLabel.label == label.strip().lower()[:40])))

    if person:
        # Scoped to this user's own people, or an id guessed from another
        # account would name whose photos came back.
        owned = db.query(Person.id).filter(Person.id == person,
                                           Person.user_id == user.id).first()
        if not owned:
            raise HTTPException(404, "Person not found")
        sel = sel.filter(GalleryPhoto.id.in_(
            db.query(PhotoPerson.photo_id).filter(PhotoPerson.person_id == person)))
    # Archive scoping. The timeline hides archived photos; `archived=1` is the
    # Archive view itself. A SEARCH still reaches them either way, which is the
    # whole point of archiving rather than deleting: it is out of the way, not
    # gone, and someone looking for a receipt should find it.
    if not smart and not term and not person:
        if archived:
            sel = sel.filter(GalleryPhoto.is_archived == 1)
        else:
            sel = sel.filter((GalleryPhoto.is_archived == 0)
                             | (GalleryPhoto.is_archived.is_(None)))

    _sort = (sort or "").strip().lower()
    if _sort == "added":
        order = [GalleryPhoto.created_at.desc(), GalleryPhoto.id.desc()]
    elif _sort == "oldest":
        # Oldest TAKEN first — the reverse of the default, for someone reading a
        # library forwards in time. Ascending id breaks ties the same direction.
        order = [GalleryPhoto.taken_at.asc(), GalleryPhoto.shot_at.asc(),
                 GalleryPhoto.id.asc()]
    else:
        order = [GalleryPhoto.taken_at.desc(), GalleryPhoto.shot_at.desc(),
                 GalleryPhoto.id.desc()]

    # shot_at sits between the two deliberately. `taken_at` is a FlexDate — a
    # DAY, with no time in it — so ordering on it alone left every photo from
    # one day tied, and the tie fell through to `id`, which is upload order.
    # An afternoon imported before the morning read back in that order for
    # ever, and re-importing could not fix it. `shot_at` is the full capture
    # timestamp (models.py) and is what actually puts a day in sequence.
    # Photos with no EXIF time have shot_at NULL and keep falling through to
    # id, which is the only honest answer for them.

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
    items = [_present(p) for p in rows]
    if bursts:
        # ANNOTATED, NOT REMOVED. Every photo still comes back on the page it
        # was on, so `total` stays true and pagination does not have to be
        # recomputed against a collapsed count; the leader carries the group
        # and the followers carry a pointer to it, and the timeline decides
        # whether to draw one tile or eight.
        #
        # A burst that straddles a page boundary is seen as two shorter runs.
        # With a 150-photo page and a minimum group of 3 that is rare, and the
        # failure is cosmetic — two badges instead of one — which is the right
        # side to be wrong on: the alternative is reading past the page to find
        # out, on every request, for every page.
        by_id = {p.id: it for p, it in zip(rows, items)}
        for run in _bursts.group(rows):
            if len(run) < _bursts.MIN_GROUP:
                continue
            lead = by_id[run[0].id]
            lead["burst_count"] = len(run)
            lead["burst_ids"] = [x.id for x in run]
            for other in run[1:]:
                by_id[other.id]["burst_of"] = run[0].id
    return {"items": items, "total": total, "offset": offset,
            "limit": limit, "mode": "smart" if smart else "text"}


@router.get("/labels")
def labels(min_count: int = 1, user: User = Depends(guard("gallery", "view")),
           db: Session = Depends(get_db)):
    """The categories this library actually contains, commonest first.

    Only labels that were found in real photos — the vocabulary has 57 entries
    and a given household will have none of most of them. Offering "desert" to
    somebody with no desert photographs is how a category browser becomes a
    list of dead ends.
    """
    rows = (db.query(PhotoLabel.label, func.count(PhotoLabel.photo_id))
            .join(GalleryPhoto, GalleryPhoto.id == PhotoLabel.photo_id)
            .filter(PhotoLabel.user_id == user.id,
                    GalleryPhoto.is_trashed == 0,
                    # Archived too. The count has to match what clicking the
                    # chip shows, and the timeline hides archived photos —
                    # "chart 17" that opens 16 is a small lie, and small lies
                    # about counts are how people stop trusting the numbers.
                    (GalleryPhoto.is_archived == 0)
                    | (GalleryPhoto.is_archived.is_(None)))
            .group_by(PhotoLabel.label).all())
    items = [{"label": l, "count": int(n)} for l, n in rows
             if int(n) >= max(1, min_count)]
    items.sort(key=lambda x: (-x["count"], x["label"]))
    return {"items": items, "total": len(items)}


@router.post("/have")
def already_have(body: dict = Body(...),
                 user: User = Depends(guard("gallery", "view")),
                 db: Session = Depends(get_db)):
    """Which of these photos does this account already hold?

    THE PROBLEM. A phone knows what it has sent because it keeps a list, and
    that list is local: clear the app's data, reinstall it, or use the "photos
    missing on the computer" reset, and the phone has to assume it has sent
    nothing. It then uploads a whole library — gigabytes over a home
    connection — for the server to recognise almost all of it and store none of
    it. The work was already avoidable; the phone just had no way to ask.

    So it asks. Hashes are cheap for a phone to compute (it reads the file it
    was going to upload anyway) and the answer is a few hundred bytes.

    Matched on `source_hash`, the hash of the bytes as the DEVICE holds them —
    not content_hash, which is taken after re-encoding and which a phone cannot
    reproduce without doing the same decode.
    """
    raw = body.get("hashes")
    if not isinstance(raw, list):
        raise HTTPException(422, "hashes must be a list")
    # Capped: this is one query, and an unbounded IN list from a client is a
    # way to make the database do arbitrary work.
    wanted = [str(h)[:64] for h in raw[:1000] if h]
    if not wanted:
        return {"have": [], "asked": 0}

    rows = (db.query(GalleryPhoto.source_hash)
            .filter(GalleryPhoto.user_id == user.id,
                    GalleryPhoto.is_trashed == 0,
                    GalleryPhoto.source_hash.in_(wanted))
            .all())
    have = sorted({r[0] for r in rows if r[0]})
    return {"have": have, "asked": len(wanted)}


@router.post("/backfill-durations")
def backfill_durations(body: dict = Body(...),
                       user: User = Depends(guard("gallery", "view")),
                       db: Session = Depends(get_db)):
    """Fill in real durations for videos stored WITHOUT one.

    Videos backed up before the phone sent its duration have a null duration and
    show "0:01", because OpenCV can't read an iPhone HEVC clip's length. The phone
    can — so it sends {source_hash: duration_ms} and we fill the gaps.

    ONLY fills a MISSING duration (null or 0). A video that already has one — every
    new upload — is never touched, so this cannot change a correct duration or a
    length the phone happens to report differently. Matched on source_hash, the same
    key /have uses.
    """
    raw = body.get("durations")
    if not isinstance(raw, dict):
        raise HTTPException(422, "durations must be an object of hash: milliseconds")
    updated = 0
    for h, ms in list(raw.items())[:1000]:
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            continue
        if ms <= 0:
            continue
        rows = (db.query(GalleryPhoto)
                .filter(GalleryPhoto.user_id == user.id,
                        GalleryPhoto.kind == "video",
                        GalleryPhoto.source_hash == str(h)[:64],
                        or_(GalleryPhoto.duration_ms.is_(None),
                            GalleryPhoto.duration_ms == 0))
                .all())
        for r in rows:
            r.duration_ms = ms
            updated += 1
    if updated:
        db.commit()
    return {"updated": updated}


def _mov_duration_from_file(path: str) -> int | None:
    """`_mov_duration_ms` for a file already on disk, without loading the mdat.

    Walks the top-level boxes by seeking over each one's size, so it never pulls
    the 100 MB media payload into memory — it reads only the small `moov` box
    wherever it sits (start of a fast-start clip, end of an iPhone one) and parses
    that. Used to repair videos stored before the container was read."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            p = 0
            while p + 8 <= size:
                f.seek(p)
                hdr = f.read(16)
                if len(hdr) < 8:
                    break
                bsize = int.from_bytes(hdr[0:4], "big")
                typ = hdr[4:8]
                if bsize == 1:              # 64-bit largesize
                    bsize = int.from_bytes(hdr[8:16], "big")
                elif bsize == 0:            # to end of file
                    bsize = size - p
                if bsize < 8:
                    break
                if typ == b"moov":
                    f.seek(p)
                    # moov is metadata, not media — kilobytes; cap defensively.
                    moov = f.read(min(bsize, 20_000_000))
                    return _mov_duration_ms(moov)
                p += bsize
    except Exception:
        return None
    return None


@router.post("/rescan-durations")
def rescan_durations(user: User = Depends(guard("gallery", "view")),
                     db: Session = Depends(get_db)):
    """Re-read every stored video's real length from its own container header.

    The phone-driven backfill only ever FILLED a missing duration, so the clips
    OpenCV had already stamped with a bogus ~1 second ("0:01") stayed wrong — and
    an iCloud-optimised video the phone never had the length for could not be
    filled at all. Reading the container works for HEVC where OpenCV does not and
    needs nothing from the phone, so one press corrects the whole library. The
    container is authoritative for the bytes we hold, so overwriting is always
    right."""
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id,
                    GalleryPhoto.kind == "video",
                    GalleryPhoto.is_trashed == 0)
            .all())
    fixed = 0
    for r in rows:
        path = storage.media_path(storage.GALLERY, user.id, storage.ORIGINAL,
                                  r.filename)
        ms = _mov_duration_from_file(path)
        if ms and ms != r.duration_ms:
            r.duration_ms = ms
            fixed += 1
    if fixed:
        db.commit()
    return {"fixed": fixed, "scanned": len(rows)}


@router.post("/repair-videos")
def repair_videos(user: User = Depends(guard("gallery", "create")),
                  db: Session = Depends(get_db)):
    """Repair videos stored before the classification and fast-start fixes.

    Two faults live in older libraries and NEITHER corrects itself on a code
    update, because the damage is in the stored file and row, not in the code:

      * HEIC photos an earlier build mistook for videos (a 0:01 clip with a play
        button that could not play, because it had no video track) — put back to
        photos by re-running the photo path, which decodes and thumbnails them
        properly, then dropping the video row and its files.
      * real clips with their moov index at the END, so a player round-tripped to
        the end of the file before it could show a frame — re-muxed to fast-start
        in place, the same proven rewrite new uploads now get.

    One press fixes a whole library and is safe to run again: an item already
    right is skipped."""
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id,
                    GalleryPhoto.kind == "video",
                    GalleryPhoto.is_trashed == 0)
            .all())
    reclassified = faststarted = 0
    for r in rows:
        path = storage.media_path(storage.GALLERY, user.id, storage.ORIGINAL,
                                  r.filename)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            raw = f.read()
        if not looks_like_video(raw, r.filename):
            # A still image mis-stored as a video. store_photo decodes HEIC,
            # thumbnails and de-duplicates correctly and commits the photo; only
            # if it did not raise do we remove the video row and its files, so an
            # image that cannot be decoded is left as it was rather than lost.
            try:
                store_photo(db, user, raw, r.orig_name or "")
            except Exception:
                continue
            storage.remove(storage.GALLERY, user.id, storage.ORIGINAL, r.filename)
            storage.remove(storage.GALLERY, user.id, storage.THUMB,
                           f"{os.path.splitext(r.filename)[0]}.jpg")
            db.delete(r)
            reclassified += 1
        else:
            fast = _faststart(raw)
            if fast:
                storage.save(storage.GALLERY, user.id, storage.ORIGINAL,
                             r.filename, fast)
                faststarted += 1
    db.commit()
    return {"reclassified": reclassified, "faststarted": faststarted,
            "scanned": len(rows)}


@router.post("/poster")
def set_poster(source_hash: str = Form(...), file: UploadFile = File(...),
               user: User = Depends(guard("gallery", "create")),
               db: Session = Depends(get_db)):
    """Accept a poster frame the PHONE rendered for a video it uploaded.

    OpenCV cannot decode iPhone HEVC, so its videos had no poster and showed the
    neutral dark tile — the "black thumbnail". The phone decodes HEVC natively and
    already holds a thumbnail, so it sends that here keyed by source_hash and it
    becomes the video's poster and thumbnail. Fixes new and existing clips alike:
    the phone re-offers a poster for anything it has backed up."""
    row = (db.query(GalleryPhoto)
           .filter(GalleryPhoto.user_id == user.id,
                   GalleryPhoto.kind == "video",
                   GalleryPhoto.source_hash == str(source_hash)[:64],
                   GalleryPhoto.is_trashed == 0)
           .first())
    if not row:
        raise HTTPException(404, "no video with that source hash")
    poster_name = f"{os.path.splitext(row.filename)[0]}.jpg"
    try:
        pil = Image.open(io.BytesIO(file.file.read()))
        pil.thumbnail((THUMB_MAX, THUMB_MAX))
        buf = io.BytesIO(); pil.convert("RGB").save(buf, format="JPEG", quality=80)
        storage.save(storage.GALLERY, user.id, storage.THUMB, poster_name,
                     buf.getvalue())
    except Exception as exc:
        raise HTTPException(422, f"could not read poster image: {exc}")
    return {"ok": True, "id": row.id}


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
                 .order_by(GalleryPhoto.taken_at.desc(), GalleryPhoto.shot_at.desc(),
                           GalleryPhoto.id.desc())
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
            .order_by(GalleryPhoto.taken_at.desc(),
                        GalleryPhoto.shot_at.desc()).all())
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
    out = []
    for a in rows:
        rule = smartalbum.parse(getattr(a, "rule", None))
        if rule:
            # A smart album's count has to be ASKED, not looked up: nothing was
            # ever filed into album_photos, so counts.get() would say 0 and the
            # album would look empty until it was opened.
            sel = smartalbum.apply(
                db.query(func.count(GalleryPhoto.id))
                .filter(GalleryPhoto.user_id == user.id,
                        GalleryPhoto.is_trashed == 0,
                        GalleryPhoto.is_archived == 0),
                db, user.id, rule)
            count = int(sel.scalar() or 0)
            cover = smartalbum.apply(
                db.query(GalleryPhoto)
                .filter(GalleryPhoto.user_id == user.id,
                        GalleryPhoto.is_trashed == 0,
                        GalleryPhoto.is_archived == 0),
                db, user.id, rule).order_by(GalleryPhoto.id.desc()).first()
            cover_url = media_url(cover.user_id, storage.THUMB, cover.filename) if cover else None
        else:
            count = int(counts.get(a.id, 0))
            cover_url = _cover_url(db, a.cover_id or latest.get(a.id))
        out.append({
            "id": a.id,
            "name": a.name,
            "count": count,
            "cover_url": cover_url,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            # The rule travels with the album so the screen can show WHY these
            # photos are here. A smart album whose rule is hidden is a folder
            # that fills itself for reasons nobody can check.
            "smart": bool(rule),
            "rule": rule or None,
            "rule_text": smartalbum.describe(rule) if rule else None,
        })
    return {"albums": out}


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
    rule = smartalbum.parse(json.dumps(body.get("rule"))
                            if isinstance(body.get("rule"), dict) else body.get("rule"))
    a = Album(user_id=user.id, name=name, created_at=now, updated_at=now,
              rule=smartalbum.dumps(rule) if rule else None)
    db.add(a); db.commit(); db.refresh(a)
    if rule:
        # A smart album takes no photo_ids even if some were sent: mixing a
        # saved query with a hand-picked list gives an album whose contents
        # nobody can predict, and no way to remove a photo the rule keeps
        # putting back.
        audit(db, user.id, "create", "album", a.id,
              {"label": name, "smart": smartalbum.describe(rule)})
        return {"id": a.id, "name": a.name, "count": 0, "smart": True,
                "rule": rule, "rule_text": smartalbum.describe(rule)}
    # Creating an album straight from a selection is the common case.
    added = _album_add(db, user.id, a, body.get("photo_ids") or [])
    audit(db, user.id, "create", "album", a.id, {"label": name, "photos": added})
    return {"id": a.id, "name": a.name, "count": added, "smart": False}


@router.get("/albums/{aid}")
def album_get(aid: int, user: User = Depends(guard("gallery", "view")),
              db: Session = Depends(get_db)):
    a = _own_album(db, user.id, aid)
    rule = smartalbum.parse(getattr(a, "rule", None))
    if rule:
        count = smartalbum.apply(
            db.query(func.count(GalleryPhoto.id))
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 0,
                    GalleryPhoto.is_archived == 0),
            db, user.id, rule).scalar()
    else:
        count = (db.query(func.count(AlbumPhoto.photo_id))
                 .join(GalleryPhoto, GalleryPhoto.id == AlbumPhoto.photo_id)
                 .filter(AlbumPhoto.album_id == aid, GalleryPhoto.is_trashed == 0).scalar())
    return {"id": a.id, "name": a.name, "count": int(count or 0),
            "smart": bool(rule), "rule": rule or None,
            "rule_text": smartalbum.describe(rule) if rule else None}


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
    if "rule" in body:
        # Passing an empty rule turns a smart album back into an ordinary one.
        # It keeps whatever is in album_photos (normally nothing), which is the
        # honest outcome: the photos it was showing were never IN it.
        raw = body.get("rule")
        rule = smartalbum.parse(json.dumps(raw) if isinstance(raw, dict) else raw)
        a.rule = smartalbum.dumps(rule) if rule else None
    a.updated_at = ist.now()
    db.commit()
    rule = smartalbum.parse(getattr(a, "rule", None))
    return {"id": a.id, "name": a.name, "smart": bool(rule), "rule": rule or None,
            "rule_text": smartalbum.describe(rule) if rule else None}


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
def upload(file: UploadFile = File(...), faces: int = 1, duration_ms: int = 0,
           album_id: int = 0,
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
    # Resolve the album before touching the file: a large upload aimed at an
    # album that is not the caller's should cost one query, not a whole decode.
    album = _own_album(db, user.id, album_id) if album_id else None

    # Peek the first chunk to tell a video (which may be far larger than a photo)
    # from a photo, so a real phone clip isn't rejected by the 30 MB photo cap.
    head = file.file.read(1024 * 1024)
    limit = MAX_VIDEO_BYTES if looks_like_video(head, file.filename or "") else MAX_BYTES
    chunks, total = [head], len(head)
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"File too large (max {limit // (1024 * 1024)} MB)")
        chunks.append(chunk)
    raw = b"".join(chunks)
    out = store_photo(db, user, raw, file.filename or "", duration_ms=duration_ms)
    # A duplicate lands in the album too: "put this photo in the album" is what
    # was asked, and the library already holding a copy is not a reason to say no.
    if album:
        _album_add(db, user.id, album, [out["item"]["id"]])
    return out


# --------------------------------------------------------------- resumable

def _partial_dir(user_id: int) -> str:
    """Where half-arrived uploads live, per owner.

    Under the media root rather than the system temp folder: a 300 MB video
    part must survive a reboot, and on several systems /tmp does not. It is
    also the volume the finished photo is going to anyway, so a part that fits
    here is one that can be completed.
    """
    d = os.path.join(storage.PRIVATE_ROOT, "partial", str(user_id))
    os.makedirs(d, exist_ok=True)
    return d


def _partial_path(user_id: int, upload_id: str) -> str:
    # The id is a client-generated uuid; take only characters that cannot
    # escape the folder. A path from a client is a path traversal waiting to
    # happen, however friendly the client.
    safe = "".join(c for c in upload_id if c.isalnum() or c in "-_")[:64]
    if not safe:
        raise HTTPException(422, "Bad upload id")
    return os.path.join(_partial_dir(user_id), safe + ".part")


@router.get("/upload/status")
def upload_status(upload_id: str,
                  user: User = Depends(guard("gallery", "create"))):
    """How many bytes of this upload the computer already holds.

    THE POINT: a 300 MB video that failed at 280 MB should not start again from
    zero. The phone asks this first and continues from `received` -- which on a
    home upstream is the difference between finishing and never finishing.

    An unknown id answers 0 rather than 404: "nothing here yet" is exactly what
    a fresh upload needs to hear, and making the client handle a 404 to learn it
    only invites getting that wrong.
    """
    path = _partial_path(user.id, upload_id)
    return {"upload_id": upload_id,
            "received": os.path.getsize(path) if os.path.exists(path) else 0}


@router.post("/upload/chunk")
async def upload_chunk(request: Request,
                       upload_id: str, offset: int = 0, total: int = 0,
                       filename: str = "", duration_ms: int = 0,
                       user: User = Depends(guard("gallery", "create")),
                       db: Session = Depends(get_db)):
    """Take one piece of a large upload, and finish it when the last one lands.

    Chunks are appended in order. `offset` is not a courtesy -- it is checked
    against what is actually on disk, because a client that resumed from the
    wrong place would produce a file that is the right SIZE and silently
    corrupt, which is far worse than a refused upload.

    The body is streamed to disk rather than read whole: holding a chunk of a
    4K video in memory is how a server on somebody's laptop runs out.
    """
    path = _partial_path(user.id, upload_id)
    have = os.path.getsize(path) if os.path.exists(path) else 0

    if offset != have:
        # Tell it where to resume from rather than just refusing. The phone can
        # act on this; "400 Bad Request" would leave it guessing.
        raise HTTPException(409, f"Expected offset {have}, got {offset}")

    limit = MAX_VIDEO_BYTES if looks_like_video(b"", filename) else MAX_BYTES
    written = have
    with open(path, "ab") as f:
        async for piece in request.stream():
            if not piece:
                continue
            written += len(piece)
            if written > limit:
                f.close()
                os.remove(path)
                raise HTTPException(
                    413, f"File too large (max {limit // (1024 * 1024)} MB)")
            f.write(piece)

    if total <= 0 or written < total:
        # More to come. Nothing is in the gallery yet, and that is correct: a
        # half-uploaded video is not a video.
        return {"upload_id": upload_id, "received": written, "complete": False}

    if written > total:
        os.remove(path)
        raise HTTPException(400, "More bytes arrived than were promised")

    with open(path, "rb") as f:
        blob = f.read()
    os.remove(path)
    out = store_photo(db, user, blob, filename or "upload", duration_ms=duration_ms)
    return {**out, "upload_id": upload_id, "received": written, "complete": True}


@router.post("/upload/abandon")
def upload_abandon(upload_id: str,
                   user: User = Depends(guard("gallery", "create"))):
    """Throw away a part-uploaded file the phone has given up on."""
    path = _partial_path(user.id, upload_id)
    if os.path.exists(path):
        os.remove(path)
    return {"upload_id": upload_id, "removed": True}


# Videos, by the container's own magic bytes rather than by filename.
#
# An extension is a claim, not a fact — a phone that renames on export, a file
# saved from a chat app, or anything typed by hand can all be wrong. Every one
# of these is read at a fixed offset in the first few bytes.
_VIDEO_MAGIC: tuple[tuple[int, bytes], ...] = (
    (4, b"ftyp"),        # MP4 / MOV / M4V — the ISO base media family
    (0, b"\x1a\x45\xdf\xa3"),   # Matroska / WebM
    (0, b"RIFF"),        # AVI (checked further below)
    (0, b"\x30\x26\xb2\x75"),   # ASF / WMV
    (0, b"FLV\x01"),
)

_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm", ".wmv", ".flv"}

# HEIC/HEIF/AVIF stills are ISO base-media files too: their header is
# `....ftyp<brand>`, the SAME box MP4/MOV use, so a bare "ftyp at offset 4" check
# calls an iPhone photo a video. Then it is stored as kind=video, shows "0:01"
# over a play button and cannot play — because it has no video track. The major
# brand is what tells a still image apart from a clip. Found on a real upload: 19
# of 20 "videos" were HEIC photos. (mif1/msf1/heics are image *sequences* — still
# not something the video player can open, so they are excluded here too.)
_IMAGE_FTYP_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx",
                      b"heif", b"mif1", b"msf1", b"avif", b"avis"}


def looks_like_video(raw: bytes, filename: str = "") -> bool:
    head = raw[:32]
    for offset, sig in _VIDEO_MAGIC:
        if head[offset:offset + len(sig)] == sig:
            # RIFF covers WAV and a dozen other things; only AVI is a video.
            if sig == b"RIFF":
                return head[8:12] == b"AVI "
            # ftyp is shared with HEIC/HEIF/AVIF still images — the brand decides.
            # An image is not a video however much its container looks like one.
            if sig == b"ftyp" and head[8:12] in _IMAGE_FTYP_BRANDS:
                return False
            return True
    # Falls back to the extension only when the bytes said nothing, so an
    # unusual container is still accepted rather than refused outright.
    return os.path.splitext(filename or "")[1].lower() in _VIDEO_EXT


def _video_poster(path: str) -> tuple[bytes | None, dict]:
    """A still from a video, and what we know about it.

    Uses OpenCV, which is ALREADY in this app and already in every customer
    build — it is here for face matching. The obvious alternative is ffmpeg,
    and it would have meant shipping a ~100 MB binary to every customer, with
    its own licensing to think about, to do a job something already present can
    do.

    The frame is taken about a tenth of the way in, not at zero: the first
    frame of a phone video is very often black or a blurred pan, and a wall of
    black thumbnails is indistinguishable from a broken gallery.
    """
    meta: dict = {}
    try:
        import cv2  # imported lazily: a photo upload should not pay for this
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None, meta
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        meta["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
        meta["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None
        if frames > 0 and fps > 0:
            meta["duration_ms"] = int(frames / fps * 1000)
        if frames > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(frames * 0.1)))
        ok, frame = cap.read()
        if not ok:
            # Seeking past the end of a variable-frame-rate clip fails; take the
            # first frame rather than giving up on a thumbnail entirely.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return None, meta
        ok, buf = cv2.imencode(".jpg", frame)
        return (buf.tobytes() if ok else None), meta
    except Exception as exc:
        # A video whose poster cannot be made is still a video worth keeping.
        print(f"[gallery] no poster frame for {os.path.basename(path)}: {exc}")
        return None, meta


def _find_atom(raw: bytes, want: bytes, start: int = 0,
               end: int | None = None) -> tuple[int, int] | None:
    """Walk ISO-BMFF boxes at one level and return (body_start, body_end) of the
    first box of type `want`, or None.

    A box is [4-byte size][4-byte type][body]. size==1 means a 64-bit size
    follows the type; size==0 means "to the end". Walking the tree is the whole
    point: the naive `raw.find(b"mvhd")` this replaces matched the bytes "mvhd"
    sitting by chance inside the media data (mdat) and read a 29-second clip as
    5.89 seconds. mvhd lives inside moov; nothing outside it counts."""
    if end is None:
        end = len(raw)
    p = start
    while p + 8 <= end:
        size = int.from_bytes(raw[p:p + 4], "big")
        typ = raw[p + 4:p + 8]
        body = p + 8
        if size == 1:                       # 64-bit largesize after the type
            size = int.from_bytes(raw[p + 8:p + 16], "big")
            body = p + 16
        elif size == 0:                     # extends to the end of the parent
            size = end - p
        if size < 8 or p + size > end:
            break                           # corrupt or truncated — stop walking
        if typ == want:
            return body, p + size
        p += size
    return None


def _mov_duration_ms(raw: bytes) -> int | None:
    """Duration from an MP4/MOV container's `mvhd` atom, WITHOUT decoding.

    The container header carries duration + timescale independently of the video
    codec, so this reads an iPhone HEVC clip's real length where OpenCV cannot —
    OpenCV can't decode HEVC and returned a bogus ~1 second, which is what left
    every clip showing "0:01". Reads the bytes already in memory; no ffmpeg.

    Walks ftyp→moov→mvhd rather than searching for the bytes: an iPhone clip
    keeps its moov at the END of the file, and a blind search hit a "mvhd"-shaped
    run inside the media data first and read 29s as 5.89s."""
    try:
        moov = _find_atom(raw, b"moov")
        if moov is None:
            return None
        mvhd = _find_atom(raw, b"mvhd", moov[0], moov[1])
        if mvhd is None:
            return None
        p = mvhd[0]
        version = raw[p]
        p += 4  # version(1) + flags(3)
        if version == 1:
            p += 16  # creation + modification times (8 + 8)
            timescale = int.from_bytes(raw[p:p + 4], "big"); p += 4
            duration = int.from_bytes(raw[p:p + 8], "big")
        else:
            p += 8   # creation + modification times (4 + 4)
            timescale = int.from_bytes(raw[p:p + 4], "big"); p += 4
            duration = int.from_bytes(raw[p:p + 4], "big")
        # Sanity: a real clip is between a frame and a day long.
        if timescale and 0 < duration < timescale * 86400:
            return int(duration / timescale * 1000)
    except Exception:
        return None
    return None


# Container atoms that hold other atoms — the ones fast-start must descend into
# to reach the chunk-offset tables. A leaf (mvhd, stco) is never opened as a box.
_MP4_CONTAINERS = frozenset(
    {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"udta", b"mvex"})


def _iter_atoms(raw: bytes, start: int, end: int):
    """Yield (type, atom_start, atom_end, body_start) for boxes at one level."""
    p = start
    while p + 8 <= end:
        size = int.from_bytes(raw[p:p + 4], "big")
        typ = bytes(raw[p + 4:p + 8])
        body = p + 8
        if size == 1:
            size = int.from_bytes(raw[p + 8:p + 16], "big")
            body = p + 16
        elif size == 0:
            size = end - p
        if size < 8 or p + size > end:
            return
        yield typ, p, p + size, body
        p += size


def _shift_chunk_offsets(buf: bytearray, shift: int, start: int, end: int) -> None:
    """Add `shift` to every stco/co64 entry within [start, end), recursing into
    container boxes. These tables record ABSOLUTE file positions of the media
    chunks; moving moov ahead of mdat slides mdat forward by len(moov), so each
    must move by the same amount or the player reads the wrong bytes."""
    for typ, _ps, pe, body in _iter_atoms(buf, start, end):
        if typ in (b"stco", b"co64"):
            n = int.from_bytes(buf[body + 4:body + 8], "big")  # after version+flags
            width = 4 if typ == b"stco" else 8
            off = body + 8
            for i in range(n):
                pos = off + i * width
                val = int.from_bytes(buf[pos:pos + width], "big") + shift
                buf[pos:pos + width] = val.to_bytes(width, "big")
        elif typ in _MP4_CONTAINERS:
            _shift_chunk_offsets(buf, shift, body, pe)


def _faststart(raw: bytes) -> bytes | None:
    """Relocate the moov atom to the front so a player can begin at once.

    An iPhone clip keeps moov (the index a player needs before it can show a
    single frame) at the END of the file, so over a network the player must
    round-trip to the end before playback starts — the delay people see. Moving
    moov to the front is a pure re-mux: no re-encode, no ffmpeg, same bytes of
    video. Verified byte-for-byte on real HEVC clips (every chunk offset locates
    identical media after the move) before this shipped.

    Returns the rewritten bytes, or None when it is already fast (H.264 exports
    usually are), has no moov/mdat, or anything looks off — the caller stores the
    original then, so a clip that cannot be safely rewritten is never corrupted."""
    try:
        atoms = list(_iter_atoms(raw, 0, len(raw)))
        moov = next((a for a in atoms if a[0] == b"moov"), None)
        mdat = next((a for a in atoms if a[0] == b"mdat"), None)
        ftyp = next((a for a in atoms if a[0] == b"ftyp"), None)
        if not moov or not mdat:
            return None
        if moov[1] < mdat[1]:
            return None  # already fast-start — moov precedes the media
        m_start, m_end = moov[1], moov[2]
        shift = m_end - m_start
        moov_buf = bytearray(raw[m_start:m_end])
        _shift_chunk_offsets(moov_buf, shift, 0, len(moov_buf))
        insert = ftyp[2] if ftyp else 0
        # ftyp | moov | (everything that was between ftyp and moov) | (tail)
        out = (raw[:insert] + bytes(moov_buf)
               + raw[insert:m_start] + raw[m_end:])
        # Never hand back something that is not still a valid, same-length file
        # with a readable duration — a silent corruption is worse than no gain.
        if len(out) != len(raw) or _mov_duration_ms(out) is None:
            return None
        return out
    except Exception:
        return None


def store_video(db: Session, user: User, raw: bytes, filename: str,
                duration_ms: int = 0) -> dict:
    """Store one video, with a still image for the grid to show.

    Deliberately shares de-duplication, trashing, albums and the signed media
    URLs with photos rather than growing a second gallery beside the first. To
    everything except the viewer, a video IS a gallery item.
    """
    # Hashes are of the bytes the DEVICE sent, NOT of what lands on disk: below,
    # the file is re-muxed to fast-start, so the two differ. content_hash keeps a
    # re-upload of the same clip de-duplicating, and source_hash lets the phone's
    # /have check recognise a clip it already sent — both are the phone's own view.
    content_hash = hashlib.sha256(raw).hexdigest()
    source_hash = content_hash
    dup = (db.query(GalleryPhoto)
           .filter(GalleryPhoto.user_id == user.id,
                   GalleryPhoto.content_hash == content_hash)
           .first())
    if dup:
        if dup.is_trashed:
            dup.is_trashed = 0
            dup.trashed_at = None
            dup.updated_at = ist.now()
            db.commit()
        return {"item": _present(dup), "faces_found": 0, "duplicate": True}

    ext = (os.path.splitext(filename or "")[1].lower() or ".mp4")[:8]
    fname = f"{uuid.uuid4().hex}{ext}"
    # Fast-start on the way in so playback begins without the player round-tripping
    # to the end for the moov index. Falls back to the original bytes untouched
    # when the clip is already fast or cannot be safely rewritten.
    storage.save(storage.GALLERY, user.id, storage.ORIGINAL, fname,
                 _faststart(raw) or raw)

    # The poster is written under the SAME name with a .jpg suffix, so the
    # thumbnail variant is found the way a photo's is and nothing downstream
    # needs to know the difference.
    poster_name = f"{os.path.splitext(fname)[0]}.jpg"
    poster, vmeta = _video_poster(storage.media_path(storage.GALLERY, user.id,
                                                     storage.ORIGINAL, fname))
    if poster:
        try:
            pil = Image.open(io.BytesIO(poster))
            pil.thumbnail((THUMB_MAX, THUMB_MAX))
            tbuf = io.BytesIO(); pil.convert("RGB").save(tbuf, format="JPEG", quality=80)
            storage.save(storage.GALLERY, user.id, storage.THUMB, poster_name,
                         tbuf.getvalue())
        except Exception as exc:
            print(f"[gallery] poster thumbnail failed: {exc}")

    now = ist.now()
    item = GalleryPhoto(
        user_id=user.id, filename=fname,
        caption=os.path.splitext(filename or "")[0] or None,
        taken_at=ist.today(), is_favorite=0, is_trashed=0, size_bytes=len(raw),
        content_hash=content_hash, source_hash=source_hash, phash=None,
        orig_name=(filename or "")[-255:] or None,
        width=vmeta.get("width"), height=vmeta.get("height"),
        # Read the length from the container header first — it is correct for HEVC
        # where OpenCV was not, and is what left every iPhone clip showing "0:01".
        # Fall back to the phone's value, then to OpenCV's only if it actually
        # decoded a frame (a poster means it could; no poster means don't trust it).
        kind="video",
        duration_ms=(_mov_duration_ms(raw) or duration_ms
                     or (vmeta.get("duration_ms") if poster else None)),
        created_at=now, updated_at=now)
    db.add(item); db.commit(); db.refresh(item)

    audit(db, user.id, "upload", "video", item.id,
          {"label": item.caption or item.orig_name or f"Video {item.id}"})
    return {"item": _present(item), "faces_found": 0, "duplicate": False}


def store_photo(db: Session, user: User, raw: bytes, filename: str,
                duration_ms: int = 0) -> dict:
    """Decode, de-duplicate, store and thumbnail one photo. The whole of upload.

    Shared with the device-token route so a photo arriving from an iPhone shortcut
    is the same photo as one dragged in here — same de-duplication, same EXIF, same
    thumbnail. Two copies of this would drift, and the half that drifted would be
    the one nobody is looking at.
    """
    if not raw:
        raise HTTPException(400, "Empty file")

    # A video takes the other path. Checked here rather than at the endpoint so
    # every caller — the web upload, the phone backup, the iPhone shortcut —
    # gets the same behaviour without three places deciding it separately.
    if looks_like_video(raw, filename):
        return store_video(db, user, raw, filename, duration_ms=duration_ms)

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
    # And the hash of what the DEVICE sent, which is the only one a phone can
    # work out for itself. See models.GalleryPhoto.source_hash.
    source_hash = hashlib.sha256(raw).hexdigest()

    # Idempotent upload: the same image (e.g. re-picked after an accidental refresh)
    # never creates a second row. A matching trashed photo is quietly restored.
    #
    # Matched on content_hash OR source_hash. content_hash is the normalised
    # encoding, which can shift a hair if the imaging library re-encodes
    # differently after an upgrade; source_hash is the raw device bytes and
    # matches only a byte-identical file — so it catches a re-upload content_hash
    # would miss and can never mistake two different files for one.
    dup = (db.query(GalleryPhoto)
           .filter(GalleryPhoto.user_id == user.id,
                   (GalleryPhoto.content_hash == content_hash) |
                   (GalleryPhoto.source_hash == source_hash))
           .first())
    if dup:
        changed = False
        if dup.is_trashed:
            dup.is_trashed = 0
            dup.trashed_at = None
            dup.updated_at = ist.now()
            changed = True
        # Self-heal the phone's backup pre-flight. Rows backfilled before this
        # code had source_hash taken from the STORED file, which for a
        # pre-optimisation JPEG is a re-encode, not the bytes the device sent —
        # so it equals content_hash (the give-away) and /api/gallery/have never
        # matched it, and the phone re-uploaded the whole library on every
        # backup. The device just handed us the true original-bytes hash; bind
        # it so /have matches next time. A source_hash only ever AVOIDS an
        # upload, never causes a wrong skip, so correcting it cannot lose a photo.
        if (not dup.source_hash or dup.source_hash == dup.content_hash) \
                and dup.source_hash != source_hash:
            dup.source_hash = source_hash
            changed = True
        if changed:
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

    # Orientation is applied HERE and nowhere above, and the placement is the
    # whole point.
    #
    # A phone writes the sensor frame and an EXIF tag saying which way up it is.
    # Nothing in this app ever read that tag, so every portrait photo taken on a
    # phone was thumbnailed on its side.
    #
    # It cannot be fixed earlier. `jpg` a few lines up is what content_hash is
    # taken from, and that hash is how the phone's /have pre-flight decides what
    # it has already sent and how the duplicate finder pairs photos up.
    # Transposing before that point would change the hash of every rotated photo
    # already in the library: the phone would stop recognising its own uploads
    # and re-send the entire camera roll, and the duplicate finder would stop
    # matching the pairs it currently finds. The stored ORIGINAL is untouched
    # too, so a download is still exactly the file the device sent, tag and all.
    upright = ImageOps.exif_transpose(pil) or pil
    thumb = upright.copy(); thumb.thumbnail((THUMB_MAX, THUMB_MAX))
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
                         content_hash=content_hash, source_hash=source_hash,
                         phash=_dhash(pil),
                         # The size as DISPLAYED, not as the sensor wrote it.
                         # The gallery lays photos out in justified rows from
                         # these two numbers, so a portrait photo reporting its
                         # landscape sensor dimensions would be given a wide
                         # slot and then fill it with a tall, upright thumbnail.
                         orig_name=orig_name,
                         width=upright.width or meta.get("width"),
                         height=upright.height or meta.get("height"),
                         camera=meta.get("camera"), lens=meta.get("lens"),
                         lat=meta.get("lat"), lon=meta.get("lon"), shot_at=shot_at,
                         created_at=now, updated_at=now)
    db.add(photo)
    try:
        db.commit()
    except IntegrityError:
        # Lost the race. The SELECT above and this INSERT are not one atomic
        # act, and /api/gallery/upload is declared `def` ON PURPOSE so FastAPI
        # runs it in the threadpool — so two devices backing up the same photo
        # genuinely do interleave here. Before uq_gallery_user_content existed,
        # both SELECTs missed, both INSERTs succeeded, and the household ended
        # up with two rows and two files for one photo.
        #
        # The unique index turns that silent duplication into this exception.
        # The other request has already committed the winning row and written
        # its own copy, so ours is the orphan: drop the files we just wrote,
        # and answer exactly as the sequential dup path above does. The caller
        # cannot tell which request won, and does not need to.
        db.rollback()
        winner = (db.query(GalleryPhoto)
                  .filter(GalleryPhoto.user_id == user.id,
                          GalleryPhoto.content_hash == content_hash)
                  .first())
        if winner is None:
            # Some OTHER constraint failed. Do not swallow it as a duplicate —
            # that would report a real write failure as a successful no-op.
            raise
        storage.remove(storage.GALLERY, user.id, storage.ORIGINAL, fname)
        storage.remove(storage.GALLERY, user.id, storage.THUMB, fname)
        return {"item": _present(winner), "faces_found": 0, "duplicate": True}
    db.refresh(photo)

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
        p.trashed_at = now
        p.updated_at = now
    db.commit()
    audit(db, user.id, "dedupe", "gallery", None, {"trashed": [p.id for p in rows]})
    return {"trashed": len(rows)}


@router.post("/bulk")
def bulk(body: dict = Body(...), user: User = Depends(guard("gallery", "edit")),
         db: Session = Depends(get_db)):
    """Apply one action to a selection of photos in a single round trip.

    Selecting a day in the timeline can easily be two hundred photos. Doing
    that as two hundred POSTs means two hundred sessions, two hundred commits
    and a progress bar the user watches crawl; worse, a tab closed halfway
    leaves the selection half-applied with nothing saying which half.

    One statement per action, so the whole selection lands or none of it does.
    Scoped to the caller's own rows by user_id in the filter rather than by
    checking afterwards, so an id belonging to somebody else simply does not
    match — it is not an error to report, it is a row that was never theirs.

    `trash` is deliberately here and `permanent` deliberately is not. A bulk
    soft-delete is undoable from the bin; a bulk hard-delete is the one action
    where a mis-click cannot be taken back, and it keeps its per-photo route.
    """
    action = str(body.get("action") or "").strip().lower()
    raw = body.get("ids") or []
    ids = [int(x) for x in raw if str(x).lstrip("-").isdigit()][:5000]
    if not ids:
        return {"changed": 0}

    sel = db.query(GalleryPhoto).filter(GalleryPhoto.user_id == user.id,
                                        GalleryPhoto.id.in_(ids))
    now = ist.now()
    if action == "favourite":
        vals = {GalleryPhoto.is_favorite: 1, GalleryPhoto.updated_at: now}
    elif action == "unfavourite":
        vals = {GalleryPhoto.is_favorite: 0, GalleryPhoto.updated_at: now}
    elif action == "trash":
        vals = {GalleryPhoto.is_trashed: 1, GalleryPhoto.trashed_at: now,
                GalleryPhoto.updated_at: now}
    elif action == "restore":
        # Clearing trashed_at matters: a restored photo that kept its stamp
        # would be swept away days later by a job that never saw the restore.
        vals = {GalleryPhoto.is_trashed: 0, GalleryPhoto.trashed_at: None,
                GalleryPhoto.updated_at: now}
    else:
        raise HTTPException(400, "Unknown action")

    n = sel.update(vals, synchronize_session=False)
    db.commit()
    audit(db, user.id, f"bulk_{action}", "gallery", None, {"count": n})
    return {"changed": n, "action": action}


@router.post("/{id}/archive")
def archive(id: int, body: dict = Body(...),
            user: User = Depends(guard("gallery", "edit")),
            db: Session = Depends(get_db)):
    """Take a photo out of the timeline without deleting anything.

    Archive and trash are different promises and must not be confused. Trash
    is "I want this gone, give me a month to change my mind" and it ends in
    the file being removed. Archive is "keep this, stop showing it to me" and
    it ends in nothing: the file, the faces, the search text and the album
    memberships are all untouched, storage is unchanged, and search still
    finds it.
    """
    p = (db.query(GalleryPhoto)
         .filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first())
    if not p:
        raise HTTPException(404, "Photo not found")
    p.is_archived = 1 if body.get("archived", True) else 0
    p.updated_at = ist.now()
    db.commit()
    audit(db, user.id, "archive" if p.is_archived else "unarchive", "photo", id,
          {"label": p.caption or p.orig_name or f"Photo {p.id}"})
    return {"id": id, "is_archived": int(p.is_archived)}


@router.post("/archive/bulk")
def archive_bulk(body: dict = Body(...),
                 user: User = Depends(guard("gallery", "edit")),
                 db: Session = Depends(get_db)):
    """Archive or unarchive a selection in one round trip.

    Same reasoning as /bulk: archiving is something people do to a day or a
    burst at a time, and two hundred POSTs is two hundred commits.
    """
    raw = body.get("ids") or []
    ids = [int(x) for x in raw if str(x).lstrip("-").isdigit()][:5000]
    if not ids:
        return {"changed": 0}
    on = 1 if body.get("archived", True) else 0
    n = (db.query(GalleryPhoto)
         .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.id.in_(ids))
         .update({GalleryPhoto.is_archived: on, GalleryPhoto.updated_at: ist.now()},
                 synchronize_session=False))
    db.commit()
    audit(db, user.id, "archive_bulk", "gallery", None,
          {"count": int(n), "archived": on})
    return {"changed": int(n), "archived": on}


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
    p.is_trashed = 1; p.trashed_at = ist.now(); db.commit()
    audit(db, user.id, "trash", "photo", id, {"label": p.caption or p.orig_name or f"Photo {p.id}"})
    return {"deleted": id}


@router.post("/{id}/restore")
def restore(id: int, user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    # Clearing the stamp is what stops a restored photo carrying its old
    # countdown and being purged days later by a job that never saw the restore.
    p.is_trashed = 0; p.trashed_at = None; db.commit()
    audit(db, user.id, "restore", "photo", id, {"label": p.caption or p.orig_name or f"Photo {p.id}"})
    return {"id": id, "restored": True}


def purge_photos(db: Session, rows: list) -> int:
    """Permanently remove photos and everything that hangs off them.

    The cascade lived twice — once in empty_trash, once in destroy — and both
    copies forgot PhotoVector, so every permanently deleted photo left its CLIP
    embedding behind for ever. One copy, so the next table added to the gallery
    has one place to be remembered.

    Files go FIRST and rows second, which is the wrong order and is kept on
    purpose for now: reversing it is a separate change with its own failure
    mode, and a file removed without its row is the recoverable direction.
    """
    if not rows:
        return 0
    ids = [p.id for p in rows]
    for p in rows:
        for variant in storage.VARIANTS:
            # thumb_name, so a video's poster goes with it. Removing by the raw
            # filename left a `.jpg` behind for every video ever deleted.
            name = thumb_name(p) if variant == storage.THUMB else p.filename
            storage.remove(storage.GALLERY, p.user_id, variant, name)
    db.query(PhotoFace).filter(PhotoFace.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(PhotoPerson).filter(PhotoPerson.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(PhotoVector).filter(PhotoVector.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(AlbumPhoto).filter(AlbumPhoto.photo_id.in_(ids)).delete(synchronize_session=False)
    db.query(Person).filter(Person.cover_id.in_(ids)).update(
        {Person.cover_id: None}, synchronize_session=False)
    db.query(Album).filter(Album.cover_id.in_(ids)).update(
        {Album.cover_id: None}, synchronize_session=False)
    db.query(GalleryPhoto).filter(GalleryPhoto.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return len(ids)


#: How long a binned photo is kept before it is really deleted. Google Photos
#: uses 30 days and people have learned to expect roughly that, so a photo
#: deleted by mistake is recoverable for a month and the disk is not held
#: hostage for ever. Nothing deletes a photo that is NOT in the bin.
TRASH_RETENTION_DAYS = 30

#: How long an interrupted chunked upload may sit in private/partial/ before it
#: is reclaimed. Generous on purpose — a phone that lost Wi-Fi mid-video should
#: still be able to resume tomorrow — but finite, which it previously was not.
PARTIAL_MAX_AGE_HOURS = 48


def sweep_trash(db: Session, days: int = TRASH_RETENTION_DAYS) -> int:
    """Permanently delete photos that have been in the bin longer than `days`.

    Rows binned before trashed_at existed have no stamp. They are STAMPED here
    and deliberately not purged on the same pass, so the countdown starts the
    first time this runs rather than retroactively — otherwise shipping this
    would delete the entire existing bin on the next restart, which is the one
    outcome nobody asked for.
    """
    now = ist.now()
    unstamped = (db.query(GalleryPhoto)
                 .filter(GalleryPhoto.is_trashed == 1,
                         GalleryPhoto.trashed_at.is_(None)).all())
    if unstamped:
        for p in unstamped:
            p.trashed_at = now
        db.commit()
        print("[sweep] started the retention clock on %d photo(s) already in the bin"
              % len(unstamped))

    cutoff = now - timedelta(days=days)
    due = (db.query(GalleryPhoto)
           .filter(GalleryPhoto.is_trashed == 1,
                   GalleryPhoto.trashed_at.isnot(None),
                   GalleryPhoto.trashed_at < cutoff).all())
    if not due:
        return 0
    n = purge_photos(db, due)
    print("[sweep] permanently deleted %d photo(s) binned before %s"
          % (n, cutoff.date()))
    return n


def sweep_partials(max_age_hours: int = PARTIAL_MAX_AGE_HOURS) -> int:
    """Delete abandoned chunked-upload parts.

    /upload/abandon was the only thing that ever removed one of these, and it
    needs a client well enough to ask. A phone that is force-quit, crashes or
    simply loses the network mid-video left a file of up to MAX_VIDEO_BYTES
    under private/partial/<uid>/ for ever — in a directory that is not in
    storage.MODULES, so it did not even appear in the storage screen. The disk
    filled with something the owner could not see or explain.
    """
    root = os.path.join(storage.PRIVATE_ROOT, "partial")
    if not os.path.isdir(root):
        return 0
    cutoff = time.time() - max_age_hours * 3600
    freed = n = 0
    for uid in os.listdir(root):
        d = os.path.join(root, uid)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".part"):
                continue
            f = os.path.join(d, name)
            try:
                if os.path.getmtime(f) >= cutoff:
                    continue
                freed += os.path.getsize(f)
                os.remove(f)
                n += 1
            except OSError:
                # Being deleted under us, or a permission fault. Either way the
                # next pass will see it again — this must never kill the tick.
                continue
    if n:
        print("[sweep] reclaimed %d abandoned upload part(s), %.1f MB"
              % (n, freed / 1048576))
    return n


@router.post("/trash/empty")
def empty_trash(user: User = Depends(guard("gallery", "delete")), db: Session = Depends(get_db)):
    """Permanently delete EVERY trashed photo for the user in one shot — removes each
    file and its face/person links, and nulls any person cover that pointed at them."""
    rows = (db.query(GalleryPhoto)
            .filter(GalleryPhoto.user_id == user.id, GalleryPhoto.is_trashed == 1).all())
    n = purge_photos(db, rows)
    if not n:
        return {"deleted": 0}
    audit(db, user.id, "empty_trash", "gallery", None, {"deleted": n})
    return {"deleted": n}


@router.delete("/{id}/permanent")
def destroy(id: int, user: User = Depends(guard("gallery", "delete")), db: Session = Depends(get_db)):
    """Permanently delete a trashed photo — removes files and every face/person link."""
    p = db.query(GalleryPhoto).filter(GalleryPhoto.id == id, GalleryPhoto.user_id == user.id).first()
    if not p:
        raise HTTPException(404, "Photo not found")
    # Read the label BEFORE the purge — afterwards the row is gone.
    label = p.caption or p.orig_name or f"Photo {p.id}"
    purge_photos(db, [p])
    audit(db, user.id, "delete", "photo", id, {"label": label})
    return {"deleted": id}
