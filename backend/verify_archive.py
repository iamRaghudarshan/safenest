"""Archive: out of the timeline, still in the library.

The distinction that matters is with TRASH. Trash means "I want this gone,
give me a month to change my mind" and it ends in the file being deleted.
Archive means "keep this, stop showing it to me" and it must end in nothing at
all — the photo keeps its files, stays in its albums, stays in storage, and is
still found by search.

Run against a throwaway instance on 8099 (see tools/screenshots/demo_env.bat).
"""
import json
import os
import sqlite3
import urllib.error
import urllib.request

B = 'http://127.0.0.1:8099'
DB = os.environ.get('SN_DEMO_DB',
                    r'C:/Users/Pro-TEAM/AppData/Local/Temp/safenest-demo/demo.db')
FAIL = []


def check(ok, label, extra=''):
    print('  %-56s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
    if not ok:
        FAIL.append(label)


def call(path, body=None, method=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method=method or ('POST' if data is not None else 'GET'),
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            return e.code, {}


st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
assert st == 200, (st, d)
TOK = d['token']
con = sqlite3.connect(DB)
uid = con.execute("SELECT id FROM users WHERE email='priya@example.com'").fetchone()[0]

for t in ('photo_people', 'photo_faces', 'gallery_photos'):
    con.execute('DELETE FROM ' + t)
con.commit()
ids = []
for i in range(6):
    con.execute("INSERT INTO gallery_photos (user_id, filename, caption, is_favorite, "
                "is_trashed, is_archived, content_hash, taken_at) "
                "VALUES (?,?,?,0,0,0,?,date('now'))",
                (uid, 'arc-%d.jpg' % i, 'seaside receipt %d' % i, 'arch-h%d' % i))
    ids.append(con.execute('SELECT last_insert_rowid()').fetchone()[0])
con.commit()
print('  seeded %d photos' % len(ids))


def timeline():
    st, d = call('/api/gallery?limit=100', tok=TOK)
    return {x['id'] for x in d.get('items', [])}, d.get('total')


def archive_view():
    st, d = call('/api/gallery?limit=100&archived=1', tok=TOK)
    return {x['id'] for x in d.get('items', [])}


def search(term):
    st, d = call('/api/gallery?limit=100&q=' + term, tok=TOK)
    return {x['id'] for x in d.get('items', [])}


start, _ = timeline()
check(len(start) == 6, 'all six start in the timeline', len(start))

print()
print('  --- archiving one ---')
st, d = call('/api/gallery/%d/archive' % ids[0], {'archived': True}, tok=TOK)
check(st == 200 and d.get('is_archived') == 1, 'archive returns the new state', d)

now, _ = timeline()
check(ids[0] not in now, 'the archived photo left the timeline')
check(len(now) == 5, 'and only that one left', len(now))
check(ids[0] in archive_view(), 'it is in the archive view')
check(archive_view() == {ids[0]}, 'the archive view holds only archived photos')

print()
print('  --- the point of archiving rather than deleting ---')
found = search('seaside')
check(ids[0] in found, 'SEARCH still finds an archived photo', len(found))

row = con.execute('SELECT is_trashed, filename FROM gallery_photos WHERE id=?',
                  (ids[0],)).fetchone()
check(row is not None, 'the row still exists')
check(row[0] == 0, 'archiving did NOT trash it')
check(row[1] == 'arc-0.jpg', 'the file reference is unchanged')

st, trash = call('/api/gallery/trash', tok=TOK)
tr = {x['id'] for x in (trash.get('items') or [])}
check(ids[0] not in tr, 'an archived photo is NOT in the bin')

print()
print('  --- unarchive ---')
st, d = call('/api/gallery/%d/archive' % ids[0], {'archived': False}, tok=TOK)
check(st == 200 and d.get('is_archived') == 0, 'unarchive returns the new state')
back, _ = timeline()
check(ids[0] in back and len(back) == 6, 'it is back in the timeline', len(back))

print()
print('  --- bulk ---')
st, d = call('/api/gallery/archive/bulk', {'ids': ids[1:4], 'archived': True}, tok=TOK)
check(st == 200 and d.get('changed') == 3, 'bulk archives exactly three', d)
now, _ = timeline()
check(len(now) == 3, 'three left the timeline', len(now))
check(archive_view() == set(ids[1:4]), 'the archive view holds exactly those three')

st, d = call('/api/gallery/archive/bulk', {'ids': ids[1:4], 'archived': False}, tok=TOK)
check(d.get('changed') == 3, 'bulk unarchive returns them')
now, _ = timeline()
check(len(now) == 6, 'all six are back', len(now))

print()
print('  --- archive and trash are independent ---')
call('/api/gallery/%d/archive' % ids[5], {'archived': True}, tok=TOK)
call('/api/gallery/%d' % ids[5], method='DELETE', tok=TOK)
st, trash = call('/api/gallery/trash', tok=TOK)
tr = {x['id'] for x in (trash.get('items') or [])}
check(ids[5] in tr, 'an archived photo can still be trashed')
check(ids[5] not in archive_view(),
      'a trashed photo is not offered in the archive view')
r = con.execute('SELECT is_archived, is_trashed FROM gallery_photos WHERE id=?',
                (ids[5],)).fetchone()
check(tuple(r) == (1, 1), 'both flags are independent and both stuck', tuple(r))

con.close()
print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
