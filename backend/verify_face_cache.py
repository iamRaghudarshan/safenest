"""Does the known-face cache actually help, and is it safe when people change?

Two questions, and the second matters more. A cache that returns a person who
has just been merged away would assign new faces to a row that no longer
exists — faster and wrong. So: measure the saving, then prove the
invalidation.

Counts real database reads rather than wall-clock, because wall-clock on one
machine with a warm page cache says very little.
"""
import os
import tempfile

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import indexer, ist, models

tmp = tempfile.mkdtemp(prefix='sn-cache-')
eng = create_engine('sqlite:///' + os.path.join(tmp, 't.db').replace(os.sep, '/'))
for t in (models.GalleryPhoto, models.Person, models.PhotoFace, models.PhotoPerson):
    t.__table__.create(bind=eng)
db = sessionmaker(bind=eng)()
now = ist.now()
UID = 1
FAIL = []

READS = {'n': 0}


@event.listens_for(eng, 'before_cursor_execute')
def _count(conn, cursor, statement, params, context, executemany):
    if 'photo_faces' in statement.lower() and statement.lstrip().lower().startswith('select'):
        READS['n'] += 1


def check(ok, label, extra=''):
    print('  %-54s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
    if not ok:
        FAIL.append(label)


# A library with a realistic number of assigned faces.
FACES = 400
people = []
for i in range(8):
    p = models.Person(user_id=UID, name='Person %d' % (i + 1), is_hidden=0, is_me=0,
                      created_at=now, updated_at=now)
    db.add(p); db.commit(); db.refresh(p)
    people.append(p)

for i in range(FACES):
    ph = models.GalleryPhoto(user_id=UID, filename='f%d.jpg' % i, is_favorite=0,
                             is_trashed=0, content_hash='c%d' % i,
                             created_at=now, updated_at=now)
    db.add(ph); db.commit(); db.refresh(ph)
    db.add(models.PhotoFace(user_id=UID, photo_id=ph.id,
                            person_id=people[i % len(people)].id,
                            embedding=b'\x00' * 256, bbox='1,2,3,4', score=0.9,
                            created_at=now))
db.commit()
print('  seeded %d assigned faces across %d people' % (FACES, len(people)))

# --- the saving -------------------------------------------------------------
indexer.invalidate_people()
PHOTOS = 200

READS['n'] = 0
for _ in range(PHOTOS):
    indexer._known_faces(db, UID)
cached_reads = READS['n']

indexer.invalidate_people()
READS['n'] = 0
for _ in range(PHOTOS):
    indexer.invalidate_people(UID)          # what the old code effectively did
    indexer._known_faces(db, UID)
uncached_reads = READS['n']

print()
print('  indexing %d photos:' % PHOTOS)
print('    before (a query per photo): %d reads of photo_faces' % uncached_reads)
print('    after  (cached per user)  : %d reads of photo_faces' % cached_reads)
check(cached_reads == 1, 'the table is read exactly once, not once per photo',
      cached_reads)
check(uncached_reads == PHOTOS, 'and the old behaviour really was per photo',
      uncached_reads)
check(len(indexer._known_faces(db, UID)) == FACES,
      'the cache holds every assigned face', len(indexer._known_faces(db, UID)))

# --- the part that makes it safe -------------------------------------------
print()
print('  --- invalidation ---')
before = len(indexer._known_faces(db, UID))
gone = people[0].id
db.query(models.PhotoFace).filter(models.PhotoFace.person_id == gone).update(
    {models.PhotoFace.person_id: people[1].id}, synchronize_session=False)
db.delete(db.query(models.Person).get(gone))
db.commit()

stale = indexer._known_faces(db, UID)
check(any(pid == gone for pid, _ in stale),
      'WITHOUT invalidation the cache still names the deleted person',
      '(this is the bug being guarded against)')

indexer.invalidate_people(UID)
fresh = indexer._known_faces(db, UID)
check(not any(pid == gone for pid, _ in fresh),
      'after invalidation the deleted person is gone from the cache')
check(len(fresh) == before, 'and no face was lost', '%d vs %d' % (len(fresh), before))

indexer.invalidate_people(UID + 99)
check(len(indexer._known_faces(db, UID)) == before,
      'invalidating another user does not clear this one')

indexer.invalidate_people()
READS['n'] = 0
indexer._known_faces(db, UID)
check(READS['n'] == 1, 'a full clear forces exactly one rebuild', READS['n'])

db.close()
print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
