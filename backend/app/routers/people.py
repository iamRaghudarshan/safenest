"""People for the gallery — auto-clustered faces, person-wise browsing, rename/merge/delete."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..models import GalleryPhoto, Person, PhotoFace, PhotoPerson, User
from ..security import guard
from .. import storage
from .gallery import _present, media_url

router = APIRouter(prefix="/api/people", tags=["people"])


@router.get("")
def index(offset: int = 0, limit: int = 120, min_photos: int = 1, q: str = "",
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
    ranked.sort(key=lambda x: (x[0].name.startswith("Person "), -x[1], x[0].name))

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
            "cover_url": media_url(photo.user_id, storage.THUMB, photo.filename) if photo else None,
        })
    return {"people": people, "total": len(ranked), "offset": offset, "limit": limit,
            "all_people": everyone}


@router.get("/{id}/photos")
def photos(id: int, user: User = Depends(guard("gallery", "view")), db: Session = Depends(get_db)):
    person = db.query(Person).filter(Person.id == id, Person.user_id == user.id).first()
    if not person:
        raise HTTPException(404, "Person not found")
    rows = (db.query(GalleryPhoto).join(PhotoPerson, PhotoPerson.photo_id == GalleryPhoto.id)
            .filter(PhotoPerson.person_id == id, GalleryPhoto.is_trashed == 0)
            .order_by(GalleryPhoto.taken_at.desc(), GalleryPhoto.id.desc()).all())
    return {"person": {"id": person.id, "name": person.name}, "items": [_present(r) for r in rows]}


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
    return {"deleted": id}
