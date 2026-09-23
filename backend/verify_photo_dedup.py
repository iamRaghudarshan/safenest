"""Does the new unique constraint actually refuse a duplicate photo row?

Builds a throwaway SQLite database from the real GalleryPhoto model, so this
tests the model definition itself rather than any running instance. Touches
nothing in production.
"""
import os
import sqlite3
import tempfile

from sqlalchemy import create_engine

from app import models

d = tempfile.mkdtemp(prefix='sn-dedup-')
db = os.path.join(d, 't.db').replace(os.sep, '/')
eng = create_engine('sqlite:///' + db)

models.GalleryPhoto.__table__.create(bind=eng)
print('  built gallery_photos from the real model at', db)

con = sqlite3.connect(db)
idx = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='gallery_photos'")]
print('  indexes created:')
for i in idx:
    print('    ', i)
ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='gallery_photos'").fetchone()[0]
ok = 'uq_gallery_user_content' in ddl
print('  constraint in the CREATE TABLE:', ok)
if ok:
    for line in ddl.splitlines():
        if 'uq_gallery_user_content' in line:
            print('   ', line.strip())
if not ok:
    raise SystemExit('  FAILED: the constraint is not on the model')


def ins(uid, h, fn):
    con.execute("INSERT INTO gallery_photos (user_id, filename, content_hash, "
                "is_favorite, is_trashed) VALUES (?,?,?,0,0)", (uid, fn, h))
    con.commit()


print()
print('  --- the duplicate it exists to stop ---')
ins(1, 'aaa', 'one.jpg')
print('  first  insert (user 1, hash aaa): stored')
try:
    ins(1, 'aaa', 'two.jpg')
    print('  !! FAILED: second insert succeeded, the race is still open')
    raise SystemExit(1)
except sqlite3.IntegrityError as e:
    print('  second insert (user 1, hash aaa): REFUSED --', e)

print()
print('  --- the two things it must NOT break ---')
try:
    ins(2, 'aaa', 'three.jpg')
    print('  two households, same photo: allowed (correct)')
except sqlite3.IntegrityError as e:
    print('  !! FAILED: blocked a second household -', e)
    raise SystemExit(1)

try:
    ins(1, None, 'four.jpg')
    ins(1, None, 'five.jpg')
    print('  two not-yet-hashed rows for one user: allowed (correct)')
except sqlite3.IntegrityError as e:
    print('  !! FAILED: NULL hashes blocked, backfilling would crash -', e)
    raise SystemExit(1)

con.close()
print()
print('  ALL PASS')
