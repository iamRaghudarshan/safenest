"""Exercise the People endpoints over HTTP, including cross-user access.

Two accounts, so section 48 ("User A cannot access User B ...") is tested by
actually trying it rather than asserted in prose.
"""
import json
import sqlite3
import urllib.error
import urllib.request

B = 'http://127.0.0.1:8099'
DB = r'C:/Users/Pro-TEAM/AppData/Local/Temp/safenest-demo/demo.db'
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


def login(email, pw):
    st, d = call('/api/auth/login', {'email': email, 'password': pw})
    assert st == 200, (st, d)
    return d['token']


TOK_A = login('priya@example.com', 'DemoHouse#2026')
TOK_B = login('other@example.com', 'OtherHouse#2026')
print('  two accounts signed in')

# Seed faces/people straight into the database: the indexer needs real images
# and real models, and what is under test here is the management layer.
con = sqlite3.connect(DB)
uid_a = con.execute("SELECT id FROM users WHERE email='priya@example.com'").fetchone()[0]
uid_b = con.execute("SELECT id FROM users WHERE email='other@example.com'").fetchone()[0]


def wipe():
    # Idempotent: a second run must not trip the (user_id, content_hash) unique
    # index, which is doing its job by refusing the same seed twice.
    for t in ('photo_people', 'photo_faces', 'people', 'gallery_photos'):
        con.execute('DELETE FROM ' + t)
    con.commit()


def seed(uid, tag):
    ph = []
    for i in range(4):
        con.execute("INSERT INTO gallery_photos (user_id, filename, is_favorite, "
                    "is_trashed, content_hash, taken_at) VALUES (?,?,0,0,?,date('now'))",
                    (uid, '%s-%d.jpg' % (tag, i), '%s-h%d' % (tag, i)))
        ph.append(con.execute('SELECT last_insert_rowid()').fetchone()[0])
    pe = []
    for n in ('Person 1', 'Alice', 'Person 2'):
        con.execute("INSERT INTO people (user_id, name, is_hidden, is_me) VALUES (?,?,0,0)", (uid, n))
        pe.append(con.execute('SELECT last_insert_rowid()').fetchone()[0])
    fc = []
    plan = [(ph[0], pe[0]), (ph[1], pe[0]), (ph[2], pe[1]), (ph[0], pe[2]), (ph[3], pe[2])]
    for pid, per in plan:
        con.execute("INSERT INTO photo_faces (user_id, photo_id, person_id, embedding, "
                    "bbox, score) VALUES (?,?,?,?,'1,2,3,4',0.9)",
                    (uid, pid, per, b'\x00' * 256))
        fc.append(con.execute('SELECT last_insert_rowid()').fetchone()[0])
    for pid, per in plan:
        con.execute("INSERT OR IGNORE INTO photo_people (photo_id, person_id) VALUES (?,?)",
                    (pid, per))
    con.commit()
    return ph, pe, fc


wipe()
PH_A, PE_A, FC_A = seed(uid_a, 'a')
PH_B, PE_B, FC_B = seed(uid_b, 'b')
print('  seeded 4 photos / 3 people / 5 faces per account')


def photo_count(uid):
    return con.execute('SELECT COUNT(*) FROM gallery_photos WHERE user_id=? AND '
                       'is_trashed=0', (uid,)).fetchone()[0]


START_A = photo_count(uid_a)

print()
print('  --- merge ---')
st, d = call('/api/people/%d/merge' % PE_A[0], {'ids': [PE_A[1]]}, tok=TOK_A)
check(st == 200 and d.get('faces_moved') == 1, 'merge moves the faces', d)
st, d = call('/api/people?limit=50', tok=TOK_A)
names = {p['name'] for p in d.get('people', [])}
check('Alice' in names, 'merged group kept the chosen name', sorted(names))
check(photo_count(uid_a) == START_A, 'no photo deleted')

print()
print('  --- faces list, then split ---')
st, d = call('/api/people/%d/faces' % PE_A[0], tok=TOK_A)
faces = d.get('items') or []
check(st == 200 and len(faces) == 3, 'faces list returns each face separately', len(faces))
check(all('face_id' in f and 'thumb_url' in f for f in faces),
      'each face has an id and a picture to choose by')
if faces:
    st, d = call('/api/people/%d/split' % PE_A[0],
                 {'face_ids': [faces[0]['face_id']], 'name': 'Bob'}, tok=TOK_A)
    check(st == 200 and d.get('faces_moved') == 1, 'split moves exactly the chosen face', d)
    check(photo_count(uid_a) == START_A, 'no photo deleted by split')

print()
print('  --- face correction, and the group-shot rule ---')
st, before = call('/api/people/%d/photos' % PE_A[2], tok=TOK_A)
n_before = len(before.get('items') or [])
st, d = call('/api/people/faces/%d/assign' % FC_A[3], {'person_id': None}, tok=TOK_A)
check(st == 200, 'a face can be detached from everybody', d)
st, after = call('/api/people/%d/photos' % PE_A[2], tok=TOK_A)
check(len(after.get('items') or []) == n_before - 1, 'that person lost exactly one photo')
check(photo_count(uid_a) == START_A, 'the photo itself survives detaching a face')

print()
print('  --- hide and me ---')
st, d = call('/api/people/%d/hide' % PE_A[2], {'hidden': True}, tok=TOK_A)
check(st == 200 and d.get('is_hidden') == 1, 'hide sets the flag')
st, vis = call('/api/people?limit=50', tok=TOK_A)
st, all_ = call('/api/people?limit=50&hidden=1', tok=TOK_A)
check(len(all_.get('people', [])) > len(vis.get('people', [])),
      'hidden person is out of the default grid but findable')
# POST with an explicit method: /me takes no body, and call() would otherwise
# default to GET and get a 405 that looks like a broken endpoint.
st, d = call('/api/people/%d/me' % PE_A[0], method='POST', tok=TOK_A)
check(st == 200 and d.get('is_me') == 1, 'a person can be marked as me', st)
st, d = call('/api/people/%d/me' % PE_A[2], method='POST', tok=TOK_A)
mes = con.execute('SELECT COUNT(*) FROM people WHERE user_id=? AND is_me=1',
                  (uid_a,)).fetchone()[0]
check(mes == 1, 'only one person is me after reassigning', mes)

print()
print('  --- section 48: user A against user B ---')
for label, path, body in (
        ('merge B\'s person', '/api/people/%d/merge' % PE_B[0], {'ids': [PE_B[1]]}),
        ('split B\'s person', '/api/people/%d/split' % PE_B[0], {'face_ids': [FC_B[0]]}),
        ('hide B\'s person', '/api/people/%d/hide' % PE_B[0], {'hidden': True}),
        ('mark B\'s person as me', '/api/people/%d/me' % PE_B[0], None),
        ('reassign B\'s face', '/api/people/faces/%d/assign' % FC_B[0], {'person_id': PE_A[0]}),
        ('set cover from B\'s photo', '/api/people/%d/cover' % PE_A[0], {'photo_id': PH_B[0]})):
    st, d = call(path, body if body is not None else {}, tok=TOK_A)
    check(st == 404, 'A cannot ' + label, st)

st, d = call('/api/people/%d/faces' % PE_B[0], tok=TOK_A)
check(st == 404, 'A cannot list B\'s faces', st)
st, d = call('/api/people/%d/photos' % PE_B[0], tok=TOK_A)
check(st in (403, 404) or not (d.get('items')), 'A cannot read B\'s person photos', st)
check(photo_count(uid_b) == 4, 'B\'s photos are all still there')

con.close()
print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
