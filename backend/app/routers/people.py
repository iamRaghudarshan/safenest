"""People for the gallery — auto-clustered faces, person-wise browsing, rename/merge/delete."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import indexer, ist
from ..database import get_db
from ..models import GalleryPhoto, Person, PhotoFace, PhotoPerson, User
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
    if cover_ids:
        covers = {ph.id: ph for ph in db.query(GalleryPhoto)
                  .filter(GalleryPhoto.id.in_(cover_ids)).all()}

    people = []
    for p, n in page:
        photo = covers.get(p.cover_id)
        people.append({
            "id": p.id, "name": p.name, "count": n,
            # The UI needs both: "me" to mark the owner's own face, and
            # "hidden" so the show-hidden view can offer to unhide.
            "is_me": int(p.is_me or 0), "is_hidden": int(p.is_hidden or 0),
            "cover_url": media_url(photo.user_id, storage.THUMB, photo.filename) if photo else None,
        })
    return {"people": people, "total": len(ranked), "offset": offset, "limit": limit,
            "all_people": everyone}


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
        out.append({"face_id": f.id, "photo_id": f.photo_id, "bbox": f.bbox,
                    "score": float(f.score) if f.score is not None else None,
                    "thumb_url": media_url(photo.user_id, storage.THUMB,
                                           thumb_name(photo))})
    return {"items": out, "total": len(out)}


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
