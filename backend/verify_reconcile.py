"""Does reconciliation actually notice drift, and refuse to make it worse?

Drift is created for real — files deleted behind the database's back, files
written with no row, derived rows left pointing at deleted photos — and then
the check has to find exactly those and nothing else. Throwaway SQLite and a
throwaway media root; production is untouched.
"""
import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ist, models, reconcile, storage

tmp = tempfile.mkdtemp(prefix='sn-recon-')
db_path = os.path.join(tmp, 't.db').replace(os.sep, '/')
eng = create_engine('sqlite:///' + db_path)
for t in (models.GalleryPhoto, models.Document, models.PhotoFace,
          models.PhotoVector, models.Person):
    t.__table__.create(bind=eng)
db = sessionmaker(bind=eng)()

storage.PRIVATE_ROOT = os.path.join(tmp, 'private')
storage.MEDIA_ROOT = storage.PRIVATE_ROOT
UID = 1
now = ist.now()
FAIL = []


def check(ok, label, extra=''):
    print('  %-54s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
    if not ok:
        FAIL.append(label)


def add_photo(name, kind='photo', write=True, thumb=True):
    p = models.GalleryPhoto(user_id=UID, filename=name, kind=kind,
                            is_favorite=0, is_trashed=0,
                            content_hash=name, created_at=now, updated_at=now)
    db.add(p); db.commit(); db.refresh(p)
    if write:
        storage.save(storage.GALLERY, UID, storage.ORIGINAL, name, b'x' * 64)
    if thumb:
        tn = f'{name}.jpg' if kind == 'video' else name
        storage.save(storage.GALLERY, UID, storage.THUMB, tn, b'x' * 32)
    return p


# --- a healthy library -----------------------------------------------------
good = [add_photo('ok-%d.jpg' % i) for i in range(5)]
vid = add_photo('clip.mp4', kind='video')
doc = models.Document(user_id=UID, title='Invoice', filename='d1.pdf',
                      is_trashed=0, created_at=now, updated_at=now)
db.add(doc); db.commit()
storage.save(storage.DOCUMENTS, UID, storage.ORIGINAL, 'd1.pdf', b'%PDF-1.4')

r = reconcile.check_user(db, UID)
check(r['clean'] is True, 'a healthy library reports clean', r['gallery'])
check(r['gallery']['missing_thumb'] == 0,
      'a video is NOT reported as a missing thumbnail', r['gallery']['missing_thumb'])

# --- now break it, the way reality breaks it -------------------------------
print()
print('  --- introducing real drift ---')

# 1. a file deleted behind the database's back
os.remove(storage.media_path(storage.GALLERY, UID, storage.ORIGINAL, good[0].filename))
# 2. a thumbnail gone but the original fine
os.remove(storage.media_path(storage.GALLERY, UID, storage.THUMB, good[1].filename))
# 3. a file on disk that no row claims
storage.save(storage.GALLERY, UID, storage.ORIGINAL, 'stray.jpg', b'x' * 64)
# 4. a document whose file is gone
os.remove(storage.media_path(storage.DOCUMENTS, UID, storage.ORIGINAL, 'd1.pdf'))
# 5. derived rows pointing at a photo that no longer exists
db.add(models.PhotoVector(photo_id=9999, user_id=UID, model='clip', vec=b'\x00' * 8,
                          created_at=now))
db.add(models.PhotoFace(user_id=UID, photo_id=9999, person_id=None,
                        embedding=b'\x00' * 256, bbox='1,2,3,4', score=0.5,
                        created_at=now))
db.commit()

r = reconcile.check_user(db, UID)
print()
check(r['clean'] is False, 'drift is detected')
check(r['gallery']['missing_file'] == 1, 'the deleted original is found',
      r['gallery']['missing_file'])
check(r['gallery']['missing_thumb'] == 1, 'the missing thumbnail is found',
      r['gallery']['missing_thumb'])
check(r['gallery']['stray_file'] == 1, 'the unclaimed file is found',
      r['gallery']['stray_file'])
check(r['documents']['missing_file'] == 1, 'the missing document is found',
      r['documents']['missing_file'])
check(r['derived']['orphan_vectors'] == 1, 'the orphan embedding is found')
check(r['derived']['orphan_faces'] == 1, 'the orphan face is found')
check(r['gallery']['samples']['missing_file'][0]['filename'] == good[0].filename,
      'the sample names the actual file')

# --- the rule: repair must not make it worse -------------------------------
print()
print('  --- repair touches only what cannot be lost ---')
files_before = len(os.listdir(storage.media_dir(storage.GALLERY, UID, storage.ORIGINAL)))
rows_before = db.query(models.GalleryPhoto).count()
docs_before = db.query(models.Document).count()

out = reconcile.repair_derived(db, UID)
check(out['vectors_removed'] == 1, 'the orphan embedding is removed', out)
check(out['faces_removed'] == 1, 'the orphan face is removed', out)

check(len(os.listdir(storage.media_dir(storage.GALLERY, UID, storage.ORIGINAL)))
      == files_before, 'NO file was deleted by repair')
check(db.query(models.GalleryPhoto).count() == rows_before,
      'NO photo row was deleted by repair')
check(db.query(models.Document).count() == docs_before,
      'NO document row was deleted by repair')

r = reconcile.check_user(db, UID)
check(r['derived']['orphan_vectors'] == 0 and r['derived']['orphan_faces'] == 0,
      'derived orphans are gone after repair')
check(r['gallery']['missing_file'] == 1 and r['gallery']['stray_file'] == 1,
      'missing and stray files are STILL reported, not silently fixed')

# --- a real face must survive ---------------------------------------------
live = models.PhotoFace(user_id=UID, photo_id=good[2].id, person_id=None,
                        embedding=b'\x00' * 256, bbox='1,2,3,4', score=0.9,
                        created_at=now)
db.add(live); db.commit()
reconcile.repair_derived(db, UID)
check(db.query(models.PhotoFace).filter(models.PhotoFace.id == live.id).first()
      is not None, 'a face belonging to a real photo is untouched')

db.close()
print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
