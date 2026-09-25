"""People for the gallery — auto-clustered faces, person-wise browsing, rename/merge/delete."""
from datetime import datetime

import re

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import indexer, ist, vision
from ..database import get_db
from ..models import GalleryPhoto, Person, PhotoFace, PhotoPerson, User
from ..helpers import audit
from ..security import guard
from .. import storage
from .gallery import _present, media_url, thumb_name

router = APIRouter(prefix="/api/people", tags=["people"])


def _is_auto(name: str | None) -> bool:
    """A name the clusterer invented, rather than one somebody chose."""
    return not name or name.strip().lower().startswith("person ")


@router.get("")
def index(offset: int = 0, limit: int = 120, min_photos: int = 1, q: str = "",
          hidden: int = 0,
          user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    """People, most-photographed first.

    Three queries flat, whatever the number of people. It used to run two per
    person — a count and a cover lookup — which on a library that had clustered
    into a few thousand faces meant thousands of round trips and a four-second
    wait before the tab would paint.
    """
    offset = max(0, offset)
    limit = min(max(1, limit), 300)

    base = db.query(Person).filter(Person.user_id == user.id)
    # Hidden people are out of the grid unless explicitly asked for, which is
    # what makes hiding mean anything. `hidden=1` is the "show hidden people"
    # view, so a mistake can be undone without going to the database.
    if not hidden:
        base = base.filter((Person.is_hidden == 0) | (Person.is_hidden.is_(None)))
    term = (q or "").strip()[:80]
    if term:
        base = base.filter(Person.name.like(f"%{term}%"))
    rows = base.all()
    # How many people exist at all, independent of this search — the People tab
    # offers "show everyone" from it, so a search that matched nothing must not
    # report that there is nobody.
    everyone = int(db.query(func.count(Person.id))
                   .filter(Person.user_id == user.id).scalar() or 0)
    if not rows:
        return {"people": [], "total": 0, "offset": offset, "limit": limit,
                "all_people": everyone}

    counts = dict(db.query(PhotoPerson.person_id, func.count(PhotoPerson.photo_id))
                  .filter(PhotoPerson.person_id.in_([p.id for p in rows]))
                  .group_by(PhotoPerson.person_id).all())

    # Named people first (someone bothered to name them), then by how many photos
    # they appear in — a face seen once is far less interesting than a regular.
    ranked = [(p, int(counts.get(p.id, 0))) for p in rows]
    ranked = [x for x in ranked if x[1] >= max(0, min_photos)]
    # "Me" first when it is set, then named people, then by how many photos.
    # Somebody's own face is the one they reach for most and it should not be
    # ranked by photo count like everybody else.
    ranked.sort(key=lambda x: (not x[0].is_me, x[0].name.startswith("Person "),
                               -x[1], x[0].name))

    page = ranked[offset:offset + limit]
    cover_ids = [p.cover_id for p, _ in page if p.cover_id]
    covers = {}
    boxes = {}
    if cover_ids:
        covers = {ph.id: ph for ph in db.query(GalleryPhoto)
                  .filter(GalleryPhoto.id.in_(cover_ids)).all()}
        # WHERE THE FACE IS in that cover photo. Without it a client can only
        # show the middle of the picture, which is why every person's circle
        # was a photograph rather than a face.
        #
        # One query for the whole page, not one per person: a people screen
        # with forty faces would otherwise be forty round trips.
        want = [(p.id, p.cover_id) for p, _ in page if p.cover_id]
        rows_f = (db.query(PhotoFace)
                  .filter(PhotoFace.user_id == user.id,
                          PhotoFace.person_id.in_([pid for pid, _ in want]),
                          PhotoFace.photo_id.in_([cid for _, cid in want]))
                  .all())
        # Keyed by BOTH ids. Keying on person alone picks whichever face row
        # came back first, which is often from a different photo than the
        # cover — and the crop then lands on nothing.
        bykey = {(f.person_id, f.photo_id): f for f in rows_f}
        for pid, cid in want:
            f = bykey.get((pid, cid))
            ph = covers.get(cid)
            if f is not None and ph is not None:
                boxes[pid] = face_box(f.bbox, ph.width, ph.height)

    people = []
    for p, n in page:
        photo = covers.get(p.cover_id)
        people.append({
            "id": p.id, "name": p.name, "count": n,
            # The UI needs both: "me" to mark the owner's own face, and
            # "hidden" so the show-hidden view can offer to unhide.
            "is_me": int(p.is_me or 0), "is_hidden": int(p.is_hidden or 0),
            "cover_url": media_url(photo.user_id, storage.THUMB, photo.filename) if photo else None,
            # Fractions of the cover photo. Null when the face cannot be
            # located, and a client that gets null should show the whole
            # picture rather than guess at a crop.
            "box": boxes.get(p.id),
        })
    return {"people": people, "total": len(ranked), "offset": offset, "limit": limit,
            "all_people": everyone}


#: How much wider than the detector's rectangle a face crop is taken.
#:
#: A box cropped exactly to what the detector returned is a nose and two eyes.
#: People recognise a face by its OUTLINE — hair, jaw, ears — so the crop is
#: widened to roughly head-and-hair.
FACE_PAD = 0.35


def face_box(bbox: str | None, width, height) -> dict | None:
    """A detector rectangle as FRACTIONS of the photo, padded, or None.

    Fractions rather than pixels because the client is showing a thumbnail of
    unknown size: pixels measured against the original would crop somewhere
    else entirely once the picture was scaled down.

    Used by BOTH the faces list and the people list. It lived only in the
    faces list, which is exactly why every person's cover circle showed the
    middle of a photograph instead of a face.
    """
    try:
        x, y, w, h = (float(v) for v in (bbox or "").split(","))
    except (ValueError, TypeError, AttributeError):
        return None
    try:
        pw, ph = float(width or 0), float(height or 0)
    except (TypeError, ValueError):
        return None
    if pw <= 0 or ph <= 0 or w <= 0 or h <= 0:
        return None
    cx, cy = x + w / 2, y + h / 2
    w2, h2 = w * (1 + FACE_PAD), h * (1 + FACE_PAD)
    return {
        "x": max(0.0, (cx - w2 / 2) / pw),
        "y": max(0.0, (cy - h2 / 2) / ph),
        "w": min(1.0, w2 / pw),
        "h": min(1.0, h2 / ph),
    }


@router.get("/{id}/photos")
def photos(id: int, offset: int = 0, limit: int = 150,
           user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    """Every photo one person appears in, a page at a time.

    It returned the lot in one answer and reported no total. That was harmless
    while the caller rendered whatever arrived, and stopped being harmless when
    the phone started paging: a caller asking for a second page got the first
    one again — offset was not a parameter — and, having no total to check
    against, had no way to tell. The face clustering here has already found
    people with 49 photos, so this is a real size, not a hypothetical one.
    """
    person = db.query(Person).filter(Person.id == id, Person.user_id == user.id).first()
    if not person:
        raise HTTPException(404, "Person not found")
    offset = max(0, offset)
    limit = min(max(1, limit), 300)
    sel = (db.query(GalleryPhoto).join(PhotoPerson, PhotoPerson.photo_id == GalleryPhoto.id)
           .filter(PhotoPerson.person_id == id, GalleryPhoto.is_trashed == 0))
    total = sel.count()
    rows = (sel.order_by(GalleryPhoto.taken_at.desc(), GalleryPhoto.id.desc())
            .offset(offset).limit(limit).all())
    return {"person": {"id": person.id, "name": person.name},
            "items": [_present(r) for r in rows],
            "total": total, "offset": offset, "limit": limit}


def _own(db: Session, uid: int, pid: int) -> Person:
    person = db.query(Person).filter(Person.id == pid, Person.user_id == uid).first()
    if not person:
        raise HTTPException(404, "Person not found")
    return person


def _relink(db: Session, person_id: int) -> None:
    """Rebuild photo<->person links for one person from its faces.

    PhotoPerson is a cache of "this photo contains this person", derived from
    PhotoFace. Every operation below moves FACES; this puts the derived table
    back in step afterwards rather than each one trying to patch it, which is
    where an off-by-one leaves a person showing a photo they are no longer in.
    """
    db.query(PhotoPerson).filter(PhotoPerson.person_id == person_id).delete(
        synchronize_session=False)
    photo_ids = {pid for (pid,) in db.query(PhotoFace.photo_id)
                 .filter(PhotoFace.person_id == person_id).distinct().all()}
    now = ist.now()
    for pid in photo_ids:
        db.add(PhotoPerson(photo_id=pid, person_id=person_id, created_at=now))


@router.post("/{id}/merge")
def merge(id: int, body: dict = Body(...),
          user: User = Depends(guard("gallery", "edit")),
          db: Session = Depends(get_db)):
    """Fold other people into this one.

    The clustering is deliberately cautious — a threshold loose enough never to
    split one person in two would also merge siblings — so the same face
    arriving at several angles becomes several groups. Merging is how a person
    fixes that, and it is the single most-used control on a People screen.

    Faces move; nothing is deleted. The emptied Person rows go, because a
    person with no faces is not a person, but every photo and every face is
    untouched.
    """
    target = _own(db, user.id, id)
    raw = body.get("ids") or []
    ids = [int(x) for x in raw if str(x).lstrip("-").isdigit() and int(x) != id]
    if not ids:
        return {"merged": 0, "id": id}
    others = (db.query(Person)
              .filter(Person.user_id == user.id, Person.id.in_(ids)).all())
    if not others:
        return {"merged": 0, "id": id}

    other_ids = [o.id for o in others]
    moved = (db.query(PhotoFace)
             .filter(PhotoFace.user_id == user.id, PhotoFace.person_id.in_(other_ids))
             .update({PhotoFace.person_id: id}, synchronize_session=False))
    db.query(PhotoPerson).filter(PhotoPerson.person_id.in_(other_ids)).delete(
        synchronize_session=False)
    # A named group absorbing an unnamed one keeps its name; an unnamed one
    # absorbing "Priya" should take that name rather than stay "Person 7".
    if _is_auto(target.name):
        named = next((o for o in others if not _is_auto(o.name)), None)
        if named:
            target.name = named.name
    for o in others:
        db.delete(o)
    _relink(db, id)
    target.updated_at = ist.now()
    db.commit()
    # The indexer caches which embeddings belong to which person. Leaving
    # it stale here would assign the next matching face to a person this
    # merge has just deleted.
    indexer.invalidate_people(user.id)
    return {"id": id, "merged": len(other_ids), "faces_moved": int(moved)}


@router.post("/{id}/split")
def split(id: int, body: dict = Body(...),
          user: User = Depends(guard("gallery", "edit")),
          db: Session = Depends(get_db)):
    """Pull the named faces out of this person into a new one.

    The other half of merge, and the one that actually repairs a bad cluster:
    two people grouped together cannot be fixed by renaming, only by taking the
    wrong faces out. Takes FACE ids, not photo ids, because a group shot
    contains several faces and only one of them is the mistake.
    """
    _own(db, user.id, id)
    raw = body.get("face_ids") or []
    face_ids = [int(x) for x in raw if str(x).lstrip("-").isdigit()]
    if not face_ids:
        raise HTTPException(422, "Choose which faces to split out")
    faces = (db.query(PhotoFace)
             .filter(PhotoFace.user_id == user.id, PhotoFace.id.in_(face_ids),
                     PhotoFace.person_id == id).all())
    if not faces:
        raise HTTPException(404, "None of those faces belong to this person")

    now = ist.now()
    count = db.query(Person).filter(Person.user_id == user.id).count()
    fresh = Person(user_id=user.id, name=(body.get("name") or "").strip()
                   or f"Person {count + 1}",
                   cover_id=faces[0].photo_id, is_hidden=0, is_me=0,
                   created_at=now, updated_at=now)
    db.add(fresh); db.flush()
    for f in faces:
        f.person_id = fresh.id
    _relink(db, id)
    _relink(db, fresh.id)
    db.commit()
    indexer.invalidate_people(user.id)
    return {"id": fresh.id, "name": fresh.name, "faces_moved": len(faces)}


@router.post("/faces/{face_id}/assign")
def assign_face(face_id: int, body: dict = Body(...),
                user: User = Depends(guard("gallery", "edit")),
                db: Session = Depends(get_db)):
    """Move one face to another person, or detach it entirely.

    "That is not Alice, that is Bob." person_id null detaches instead, for the
    cases that are not a person at all — a face on a poster, a reflection, the
    photo on somebody's ID card.

    Only the LINK changes. The face, its embedding and the photo are all left
    exactly as they are, so a correction can be corrected again.
    """
    face = (db.query(PhotoFace)
            .filter(PhotoFace.id == face_id, PhotoFace.user_id == user.id).first())
    if not face:
        raise HTTPException(404, "Face not found")
    was = face.person_id
    raw = body.get("person_id")
    target = int(raw) if raw else None
    if target is not None:
        _own(db, user.id, target)
    face.person_id = target
    db.flush()
    for pid in {x for x in (was, target) if x}:
        _relink(db, pid)
    db.commit()
    indexer.invalidate_people(user.id)
    return {"face_id": face_id, "from": was, "to": target}


@router.post("/{id}/cover")
def set_cover(id: int, body: dict = Body(...),
              user: User = Depends(guard("gallery", "edit")),
              db: Session = Depends(get_db)):
    """Choose which photo represents this person."""
    person = _own(db, user.id, id)
    photo_id = int(body.get("photo_id") or 0)
    owned = (db.query(GalleryPhoto)
             .filter(GalleryPhoto.id == photo_id,
                     GalleryPhoto.user_id == user.id).first())
    if not owned:
        raise HTTPException(404, "Photo not found")
    person.cover_id = photo_id
    person.updated_at = ist.now()
    db.commit()
    return {"id": id, "cover_id": photo_id}


@router.post("/{id}/hide")
def hide(id: int, body: dict = Body(...),
         user: User = Depends(guard("gallery", "edit")),
         db: Session = Depends(get_db)):
    """Take a person out of the People grid without unpicking the grouping.

    For the faces that are technically correct and not wanted: a stranger in
    the background of a holiday, a face on a passing poster. Deleting the
    person would discard the clustering and the next indexing pass would
    simply recreate it.
    """
    person = _own(db, user.id, id)
    person.is_hidden = 1 if body.get("hidden", True) else 0
    person.updated_at = ist.now()
    db.commit()
    return {"id": id, "is_hidden": int(person.is_hidden)}


@router.post("/{id}/me")
def mark_me(id: int, user: User = Depends(guard("gallery", "edit")),
            db: Session = Depends(get_db)):
    """Say which of these people is the owner.

    Cleared from everybody else first: two "me"s makes "photos of me"
    meaningless, and the second one is always the mistake.
    """
    person = _own(db, user.id, id)
    db.query(Person).filter(Person.user_id == user.id, Person.id != id).update(
        {Person.is_me: 0}, synchronize_session=False)
    person.is_me = 1
    person.updated_at = ist.now()
    db.commit()
    return {"id": id, "is_me": 1}


@router.get("/{id}/faces")
def faces(id: int, user: User = Depends(guard("gallery", "view")),
          db: Session = Depends(get_db)):
    """Every face in this group, so a person can pick the wrong ones out.

    Splitting needs face ids and a picture of each face to choose by; without
    this the correction tools exist but there is no way to aim them.
    """
    _own(db, user.id, id)
    rows = (db.query(PhotoFace)
            .filter(PhotoFace.user_id == user.id, PhotoFace.person_id == id,
                    PhotoFace.embedding.isnot(None))
            .order_by(PhotoFace.score.desc().nullslast(), PhotoFace.id.desc())
            .limit(500).all())
    out = []
    for f in rows:
        photo = db.query(GalleryPhoto).get(f.photo_id)
        if not photo or photo.is_trashed:
            continue
        # The box as FRACTIONS of the image, not pixels.
        #
        # The stored bbox is in the ORIGINAL photo's pixels, and what the UI
        # displays is a scaled-down thumbnail — so pixels are meaningless to
        # it without also knowing both sizes. Fractions survive any scaling,
        # which lets the browser crop to the face with no second request and
        # no image processing on this side.
        box = None
        box = face_box(f.bbox, photo.width, photo.height)

        out.append({"face_id": f.id, "photo_id": f.photo_id, "bbox": f.bbox,
                    "box": box,
                    "score": float(f.score) if f.score is not None else None,
                    "thumb_url": media_url(photo.user_id, storage.THUMB,
                                           thumb_name(photo))})
    return {"items": out, "total": len(out)}


@router.post("/regroup")
def regroup(body: dict = Body(default={}),
            user: User = Depends(guard("gallery", "edit")),
            db: Session = Depends(get_db)):
    """Group every known face again, from the embeddings already stored.

    WHY THIS IS NEEDED AT ALL. Grouping happens once, as each photo is
    indexed, and it never revisits its own decisions. So a library grouped by
    an older, worse rule stays grouped that way for ever — improving the rule
    does nothing for the photographs already in. This applies the current rule
    to everything.

    It is FAST because it touches no images: every face's embedding is already
    in the database, and this is arithmetic over numbers that are already
    there. Re-indexing, by contrast, decodes every photograph again.

    NAMES SURVIVE, and that is the part worth being careful about. Naming a
    face is the one piece of real work a person does here, and an operation
    that silently threw it away would be worse than the bad grouping it was
    meant to fix. Named people are kept as ANCHORS: every face is offered to
    them first, and only faces that match nobody named are clustered among
    themselves.

    `dry_run` reports what would change without changing it — this rewrites
    every grouping in the library, and seeing the number first is the
    difference between a decision and a surprise.
    """
    dry = bool(body.get("dry_run"))

    faces = (db.query(PhotoFace)
             .filter(PhotoFace.user_id == user.id,
                     PhotoFace.embedding.isnot(None))
             .order_by(PhotoFace.id.asc()).all())
    if not faces:
        return {"faces": 0, "people_before": 0, "people_after": 0,
                "moved": 0, "dry_run": dry,
                "note": "No faces have been found yet."}

    people = db.query(Person).filter(Person.user_id == user.id).all()
    before = len(people)
    named = {p.id: p for p in people if not _looks_unnamed(p.name)}

    vecs = {f.id: vision.unpack(f.embedding, vision.FACE_DIM) for f in faces}

    # Anchors first: every named person keeps the faces that still match them.
    groups: dict = {pid: [] for pid in named}
    for f in faces:
        if f.person_id in named:
            groups[f.person_id].append(f.id)

    # Then every face is placed again — including the ones already sitting
    # under a name, because a face that was wrongly filed there should be
    # allowed to leave.
    assigned: dict = {}
    order = sorted(faces, key=lambda f: (f.person_id not in named, f.id))
    for f in order:
        vec = vecs[f.id]
        best_id, best_score, runner_up = _score_groups(vec, groups, vecs)
        if (best_id is not None
                and best_score >= indexer.FACE_MATCH
                and best_score - runner_up >= indexer.FACE_MARGIN):
            assigned[f.id] = best_id
            if f.id not in groups[best_id]:
                groups[best_id].append(f.id)
        else:
            # A new group, keyed by a negative number so it cannot collide
            # with a real person id while the pass is running.
            new_key = -(len([k for k in groups if isinstance(k, int) and k < 0]) + 1)
            groups[new_key] = [f.id]
            assigned[f.id] = new_key

    moved = sum(1 for f in faces
                if (assigned.get(f.id) if assigned.get(f.id, 0) > 0 else None)
                != f.person_id)
    after = len([k for k, v in groups.items() if v])

    if dry:
        return {"faces": len(faces), "people_before": before,
                "people_after": after, "moved": moved, "dry_run": True}

    # ---- write it -------------------------------------------------------
    now = ist.now()
    made = {}
    for key, ids in groups.items():
        if not ids:
            continue
        if key > 0:
            continue                       # an existing named person
        count = db.query(Person).filter(Person.user_id == user.id).count()
        person = Person(user_id=user.id, name=f"Person {count + 1}",
                        created_at=now, updated_at=now)
        db.add(person)
        db.flush()
        made[key] = person.id

    for f in faces:
        key = assigned.get(f.id)
        f.person_id = made.get(key, key) if key is not None else None

    # A person left holding nothing is not a person. Named ones are kept even
    # when empty: the name is somebody's work, and an empty group is one drag
    # away from being useful again, where a deleted one is gone.
    db.flush()
    for p in people:
        still = db.query(PhotoFace).filter(PhotoFace.person_id == p.id).count()
        if still == 0 and _looks_unnamed(p.name):
            db.delete(p)

    # PhotoPerson is derived from PhotoFace, so it is rebuilt wholesale rather
    # than patched — patching is where an off-by-one leaves somebody showing a
    # photo they are not in.
    db.query(PhotoPerson).filter(
        PhotoPerson.person_id.in_(
            db.query(Person.id).filter(Person.user_id == user.id))
    ).delete(synchronize_session=False)
    seen = set()
    for f in faces:
        if f.person_id and (f.photo_id, f.person_id) not in seen:
            seen.add((f.photo_id, f.person_id))
            db.add(PhotoPerson(photo_id=f.photo_id, person_id=f.person_id,
                               created_at=now))
    db.commit()

    # The indexer caches every known face for matching; leaving it stale would
    # have the next photo grouped against the arrangement this just replaced.
    indexer.invalidate_people(user.id)

    audit(db, user.id, "regroup", "person", None,
          {"faces": len(faces), "before": before, "after": after})
    return {"faces": len(faces), "people_before": before,
            "people_after": after, "moved": moved, "dry_run": False}


def _looks_unnamed(name: str | None) -> bool:
    """A name the clustering made up, rather than one a person chose."""
    return bool(re.match(r"^person\s*\d+$", (name or "").strip(), re.I))


def _score_groups(vec, groups: dict, vecs: dict) -> tuple:
    """Best group for this face, its score, and the runner-up's.

    Scored the same way the indexer scores: the mean of a group's best few
    matches, so one outlier cannot carry a decision. Both must agree, or a
    re-group would immediately undo what the indexer does next.
    """
    scored = []
    for key, ids in groups.items():
        if not ids:
            continue
        sims = sorted((vision.cosine(vec, vecs[i]) for i in ids), reverse=True)
        top = sims[:indexer.FACE_TOP_K]
        if top:
            scored.append((sum(top) / len(top), key))
    if not scored:
        return None, 0.0, 0.0
    scored.sort(reverse=True)
    return scored[0][1], scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)


@router.put("/{id}")
def rename(id: int, body: dict = Body(...), user: User = Depends(guard("gallery", "edit")), db: Session = Depends(get_db)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Name is required")
    person = db.query(Person).filter(Person.id == id, Person.user_id == user.id).first()
    if not person:
        raise HTTPException(404, "Person not found")
    person.name = name; person.updated_at = ist.now()
    db.commit()
    return {"id": id, "name": name}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("gallery", "delete")), db: Session = Depends(get_db)):
    person = db.query(Person).filter(Person.id == id, Person.user_id == user.id).first()
    if not person:
        raise HTTPException(404, "Person not found")
    db.query(PhotoPerson).filter(PhotoPerson.person_id == id).delete()
    db.query(PhotoFace).filter(PhotoFace.person_id == id).update({PhotoFace.person_id: None})
    db.delete(person); db.commit()
    indexer.invalidate_people(user.id)
    return {"deleted": id}
