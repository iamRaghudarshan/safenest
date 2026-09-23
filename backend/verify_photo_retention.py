"""Does the retention sweep delete the right photos, and only those?

Throwaway SQLite + a throwaway media root. Production is not touched.
"""
import os
import tempfile
import time
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ist, models, storage
from app.routers import gallery

tmp = tempfile.mkdtemp(prefix='sn-sweep-')
db_path = os.path.join(tmp, 't.db').replace(os.sep, '/')
eng = create_engine('sqlite:///' + db_path)
models.Base.metadata.create_all(bind=eng)
db = sessionmaker(bind=eng)()

# keep the sweep's file removals inside the sandbox
storage.PRIVATE_ROOT = os.path.join(tmp, 'private')
storage.MEDIA_ROOT = os.path.join(tmp, 'private')
os.makedirs(storage.PRIVATE_ROOT, exist_ok=True)

now = ist.now()


def add(fn, trashed, stamp):
    p = models.GalleryPhoto(user_id=1, filename=fn, is_favorite=0,
                            is_trashed=trashed, trashed_at=stamp,
                            content_hash=fn, created_at=now, updated_at=now)
    db.add(p)
    return p


add('live.jpg', 0, None)                              # not in the bin
add('legacy.jpg', 1, None)                            # binned before the column existed
add('yesterday.jpg', 1, now - timedelta(days=1))      # binned recently
add('old.jpg', 1, now - timedelta(days=31))           # past retention
add('ancient.jpg', 1, now - timedelta(days=400))      # long past
db.commit()
print('  seeded 5 photos (1 live, 4 binned)')

print()
print('  --- FIRST PASS: the one that ships with the upgrade ---')
n = gallery.sweep_trash(db)
print('  deleted this pass:', n)

left = {p.filename: p for p in db.query(models.GalleryPhoto).all()}
print('  survivors:', sorted(left))

assert 'legacy.jpg' in left, 'FAILED: purged a photo whose clock had not started'
assert left['legacy.jpg'].trashed_at is not None, 'FAILED: legacy row not stamped'
assert 'yesterday.jpg' in left, 'FAILED: purged a photo binned yesterday'
assert 'live.jpg' in left, 'FAILED: purged a photo that was NOT in the bin'
assert 'old.jpg' not in left, 'FAILED: kept a photo past retention'
assert 'ancient.jpg' not in left, 'FAILED: kept a long-expired photo'
print('  OK: expired photos gone; live, recent and just-stamped all kept')

print()
print('  --- SECOND PASS, same day: must be a no-op ---')
n2 = gallery.sweep_trash(db)
print('  deleted this pass:', n2)
assert n2 == 0, 'FAILED: second pass deleted %d more' % n2
assert len(db.query(models.GalleryPhoto).all()) == 3
print('  OK: nothing further removed')

print()
print('  --- the legacy row only expires 30 days AFTER being stamped ---')
lg = db.query(models.GalleryPhoto).filter_by(filename='legacy.jpg').first()
lg.trashed_at = now - timedelta(days=31)
db.commit()
gallery.sweep_trash(db)
left = {p.filename for p in db.query(models.GalleryPhoto).all()}
assert 'legacy.jpg' not in left, 'FAILED: stamped row never expires'
print('  OK: it expires once its own clock runs out, not before')

print()
print('  --- abandoned upload parts ---')
pd = os.path.join(storage.PRIVATE_ROOT, 'partial', '1')
os.makedirs(pd, exist_ok=True)
fresh = os.path.join(pd, 'fresh.part')
stale = os.path.join(pd, 'stale.part')
for f in (fresh, stale):
    with open(f, 'wb') as fh:
        fh.write(b'x' * 2048)
old_t = time.time() - (gallery.PARTIAL_MAX_AGE_HOURS + 5) * 3600
os.utime(stale, (old_t, old_t))

n3 = gallery.sweep_partials()
print('  parts reclaimed:', n3)
assert os.path.exists(fresh), 'FAILED: deleted an upload still resumable'
assert not os.path.exists(stale), 'FAILED: left an abandoned part behind'
print('  OK: stale part reclaimed, resumable one untouched')

db.close()
print()
print('  ALL PASS')
