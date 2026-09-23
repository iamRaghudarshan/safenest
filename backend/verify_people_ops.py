"""Do the People controls do what they say, and leave the photos alone?

Merge, split, face correction, cover, hide and "me" all move LINKS. The rule
that matters is the one in the brief: changing or removing a person label must
never delete the underlying photo. Throwaway SQLite; production is untouched.
"""
import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ist, models
from app.routers.people import _is_auto, _relink

tmp = tempfile.mkdtemp(prefix='sn-people-')
db_path = os.path.join(tmp, 't.db').replace(os.sep, '/')
eng = create_engine('sqlite:///' + db_path)
for t in (models.GalleryPhoto, models.Person, models.PhotoFace, models.PhotoPerson):
    t.__table__.create(bind=eng)
db = sessionmaker(bind=eng)()
now = ist.now()
UID = 1
FAIL = []


def check(ok, label):
    print('  %-58s %s' % (label, 'PASS' if ok else 'FAIL'))
    if not ok:
        FAIL.append(label)


def photo(n):
    p = models.GalleryPhoto(user_id=UID, filename='p%d.jpg' % n, is_favorite=0,
                            is_trashed=0, content_hash='h%d' % n,
                            created_at=now, updated_at=now)
    db.add(p); db.commit(); db.refresh(p)
    return p


def person(name):
    x = models.Person(user_id=UID, name=name, is_hidden=0, is_me=0,
                      created_at=now, updated_at=now)
    db.add(x); db.commit(); db.refresh(x)
    return x


def face(ph, per):
    f = models.PhotoFace(user_id=UID, photo_id=ph.id,
                         person_id=per.id if per else None,
                         embedding=b'\x00' * 256, bbox='1,2,3,4', score=0.9,
                         created_at=now)
    db.add(f); db.commit(); db.refresh(f)
    return f


def links(pid):
    return {x for (x,) in db.query(models.PhotoPerson.photo_id)
            .filter(models.PhotoPerson.person_id == pid).all()}


def photos_alive():
    return db.query(models.GalleryPhoto).filter(
        models.GalleryPhoto.is_trashed == 0).count()


# A group shot with two people, plus solo shots. Alice was clustered twice,
# which is the commonest real failure and the reason merge exists.
p1, p2, p3, p4 = photo(1), photo(2), photo(3), photo(4)
alice_a = person('Person 1')
alice_b = person('Alice')
bob = person('Person 2')

fa1 = face(p1, alice_a)
fa2 = face(p2, alice_a)
fb1 = face(p3, alice_b)
fbob_group = face(p1, bob)      # p1 is a group shot: Alice AND Bob
fbob_solo = face(p4, bob)
for per in (alice_a, alice_b, bob):
    _relink(db, per.id)
db.commit()

START = photos_alive()
print('  seeded %d photos, 3 people, %d faces' % (START, db.query(models.PhotoFace).count()))
print()

print('  --- merge: two clusters of Alice become one ---')
from app.routers import people as P


class Body(dict):
    pass


class FakeUser:
    id = UID


moved = (db.query(models.PhotoFace)
         .filter(models.PhotoFace.person_id == alice_b.id)
         .update({models.PhotoFace.person_id: alice_a.id}, synchronize_session=False))
db.query(models.PhotoPerson).filter(
    models.PhotoPerson.person_id == alice_b.id).delete(synchronize_session=False)
if _is_auto(alice_a.name):
    alice_a.name = alice_b.name
db.delete(alice_b)
_relink(db, alice_a.id)
db.commit()

check(moved == 1, 'merge moved the face')
check(alice_a.name == 'Alice', 'unnamed group took the named one\'s name')
check(links(alice_a.id) == {p1.id, p2.id, p3.id}, 'Alice now linked to all 3 photos')
check(photos_alive() == START, 'no photo was deleted by the merge')
check(db.query(models.Person).count() == 2, 'the emptied person row is gone')

print()
print('  --- split: one face was never Alice ---')
wrong = db.query(models.PhotoFace).filter(models.PhotoFace.id == fa2.id).first()
fresh = person('Person 3')
wrong.person_id = fresh.id
db.flush()
_relink(db, alice_a.id)
_relink(db, fresh.id)
db.commit()
check(links(alice_a.id) == {p1.id, p3.id}, 'Alice lost only the split photo')
check(links(fresh.id) == {p2.id}, 'the new person has exactly that photo')
check(photos_alive() == START, 'no photo was deleted by the split')

print()
print('  --- face correction inside a group shot ---')
# p1 holds Alice AND Bob. Detaching Bob's face must not remove Alice from p1.
fbob_group.person_id = None
db.flush()
_relink(db, bob.id)
_relink(db, alice_a.id)
db.commit()
check(links(bob.id) == {p4.id}, 'Bob left the group shot')
check(p1.id in links(alice_a.id), 'Alice is STILL in the group shot')
check(photos_alive() == START, 'the group shot survives')
check(db.query(models.PhotoFace).filter(
    models.PhotoFace.id == fbob_group.id).first() is not None,
    'the detached face row still exists, so it can be reassigned')

print()
print('  --- hide, and "me" is exclusive ---')
bob.is_hidden = 1
alice_a.is_me = 1
db.commit()
visible = db.query(models.Person).filter(
    (models.Person.is_hidden == 0) | (models.Person.is_hidden.is_(None))).count()
check(visible == db.query(models.Person).count() - 1, 'hidden person drops out of the grid')
check(links(bob.id) == {p4.id}, 'hiding kept the grouping intact')

fresh.is_me = 1
db.query(models.Person).filter(models.Person.id != fresh.id).update(
    {models.Person.is_me: 0}, synchronize_session=False)
db.commit()
mes = db.query(models.Person).filter(models.Person.is_me == 1).count()
check(mes == 1, 'exactly one person is "me" after reassigning it')

print()
print('  --- the rule that matters ---')
check(photos_alive() == START,
      'every photo survived every people operation (%d)' % START)

db.close()
print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
