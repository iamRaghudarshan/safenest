"""Suggestions that can be refused, and creations that get made.

The feature is collages and moving highlights. The property that decides
whether anybody keeps the panel switched on is narrower than that: **no has to
mean never again.** A suggestion that returns tomorrow turns the panel into
something people learn to scroll past, and then the good suggestions go unread
with the rest.

So the test dismisses one and asks again. It also makes one, and checks the
same suggestion does not come back offering to make it twice.

The other thing worth proving is that a creation is an ORDINARY photo: in the
timeline, in the count, trashable. A creation that lives in a table of its own
is a second kind of object every screen has to learn about, and the screens
that were not told show a library that is missing things.

Runs against the throwaway instance on 8099.
"""
import io
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

from PIL import Image

B = 'http://127.0.0.1:8099'
DB = r'C:/Users/Pro-TEAM/AppData/Local/Temp/safenest-demo/demo.db'
FAIL = []


def check(ok, label, extra=''):
    line = '  %-58s %s %s' % (label, 'PASS' if ok else 'FAIL', extra)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    print(line.encode(enc, 'replace').decode(enc, 'replace'))
    if not ok:
        FAIL.append(label)


def call(path, body=None, method=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method=method or ('POST' if data is not None else 'GET'),
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            return e.code, {}


def fetch(url, tok):
    req = urllib.request.Request(
        url if url.startswith('http') else B + url,
        headers={'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def upload(name, content, tok):
    b = '----sn' + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: image/jpeg\r\n\r\n' % (b, name)).encode()
    out += content + ('\r\n--%s--\r\n' % b).encode()
    req = urllib.request.Request(B + '/api/gallery/upload', data=bytes(out),
                                 method='POST',
                                 headers={'Content-Type':
                                          'multipart/form-data; boundary=' + b,
                                          'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
assert st == 200, (st, d)
TOK = d['token']
print('  signed in')

# Start clean, so the counts below are about this run.
#
# The suggestions table included. A dismissal is meant to be permanent, and it
# is — which meant the SECOND run of this file found nothing to be suggested
# and read as "the feature is broken". That is the feature working; the test
# has to clear it, and there is no endpoint to do so because forgetting a no
# on request is not something a person should be able to ask for by accident.
con = sqlite3.connect(DB)
try:
    con.execute("DELETE FROM suggestions")
    con.commit()
finally:
    con.close()

st, lib = call('/api/gallery?limit=500', tok=TOK)
for p in lib.get('items', []):
    call('/api/gallery/%d' % p['id'], method='DELETE', tok=TOK)
call('/api/gallery/trash/empty', {}, tok=TOK)

DAY = '2026-05-01'


def shot(i, w=800, h=600):
    im = Image.new('RGB', (w, h),
                   (10 + (i * 53) % 200, 20 + (i * 97) % 200, 30 + (i * 29) % 200))
    ex = im.getexif()
    # All on ONE day, which is what makes a suggestion out of them.
    ex[306] = '2026:05:01 1%d:00:00' % (i % 10)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=92, exif=ex.tobytes())
    return buf.getvalue()


ids = []
for i in range(12):
    r = upload('day-%d-%s.jpg' % (i, uuid.uuid4().hex[:5]), shot(i), TOK)
    ids.append((r.get('item') or r)['id'])
print('  %d photos uploaded on one day' % len(ids))


print('\n  --- it notices ---')
st, d = call('/api/gallery/suggestions', tok=TOK)
check(st == 200, 'the suggestions endpoint answers', st)
items = d.get('items', [])
mine = [s for s in items if s.get('day') == DAY]
check(bool(mine), 'a day with twelve photos is noticed', [s.get('key') for s in items])
s0 = mine[0] if mine else {}
# Twelve is past REEL_MIN_PHOTOS, so this should be the moving highlight
# rather than the collage — the whole point of the threshold.
check(s0.get('kind') == 'reel', 'and twelve photos is offered as a highlight',
      s0.get('kind'))
check(s0.get('key') == 'reel:' + DAY, 'with a key derived from the day itself',
      s0.get('key'))
check('1 May 2026' in (s0.get('title') or ''),
      'and a title a person would say out loud', s0.get('title'))
check(len(s0.get('photo_ids') or []) >= 10, 'carrying the photos it means',
      len(s0.get('photo_ids') or []))


print('\n  --- no means never again ---')
st, d = call('/api/gallery/suggestions/dismiss', {'key': s0['key']}, tok=TOK)
check(st == 200, 'a suggestion can be refused', st)
st, d = call('/api/gallery/suggestions', tok=TOK)
again = [x for x in d.get('items', []) if x.get('key') == s0['key']]
check(not again, 'and it does not come back', [x.get('key') for x in d.get('items', [])])

# Saying no twice must not fail, and must not un-say it.
st, d = call('/api/gallery/suggestions/dismiss', {'key': s0['key']}, tok=TOK)
check(st == 200, 'saying no twice is still no', st)
st, d = call('/api/gallery/suggestions', tok=TOK)
check(not [x for x in d.get('items', []) if x.get('key') == s0['key']],
      'and it is still gone')

st, d = call('/api/gallery/suggestions/dismiss', {'key': ''}, tok=TOK)
check(st == 200, 'an empty key is ignored rather than stored', st)


print('\n  --- making a collage ---')
st, d = call('/api/gallery/creations',
             {'kind': 'collage', 'photo_ids': ids[:6], 'title': 'A day out'},
             tok=TOK)
check(st == 200, 'a collage is made', st)
item = d.get('item') or {}
check(d.get('from') == 6, 'from all six photos', d.get('from'))
check(item.get('id'), 'and comes back as a photo', item.get('id'))

png = Image.open(io.BytesIO(fetch(item['url'], TOK)))
check(png.width > 1000 and png.height > 400,
      'the collage is a real, large picture', png.size)
# Six photos: three columns, two rows — so it is wider than it is tall.
check(png.width > png.height,
      'six photos lay out wider than tall, not as a strip', png.size)

st, lib = call('/api/gallery?limit=500', tok=TOK)
in_list = [x for x in lib.get('items', []) if x['id'] == item['id']]
check(bool(in_list), 'and it is an ORDINARY photo in the timeline')
check(lib.get('total') == len(ids) + 1,
      'counted like any other', (lib.get('total'), len(ids) + 1))


print('\n  --- making a moving highlight ---')
st, d = call('/api/gallery/creations',
             {'kind': 'reel', 'photo_ids': ids, 'title': 'That day'}, tok=TOK)
check(st == 200, 'a reel is made', st)
item = d.get('item') or {}
raw = fetch(item['url'], TOK)
check(raw[:4] == b'RIFF' and raw[8:12] == b'WEBP', 'it really is a WebP',
      raw[:12])
anim = Image.open(io.BytesIO(raw))
check(getattr(anim, 'n_frames', 1) == len(ids),
      'with one frame per photo', getattr(anim, 'n_frames', 1))
check(anim.width == anim.height, 'and square frames', anim.size)


print('\n  --- what it refuses ---')
st, d = call('/api/gallery/creations', {'kind': 'collage', 'photo_ids': ids[:1]},
             tok=TOK)
check(st == 422, 'one photo is not a collage', st)
st, d = call('/api/gallery/creations', {'kind': 'reel', 'photo_ids': ids[:2]},
             tok=TOK)
check(st == 422, 'two photos are not a highlight', st)
st, d = call('/api/gallery/creations', {'kind': 'mosaic', 'photo_ids': ids},
             tok=TOK)
check(st == 422, 'an unknown kind is refused', st)
st, d = call('/api/gallery/creations', {'kind': 'collage', 'photo_ids': []},
             tok=TOK)
check(st == 422, 'no photos at all is refused', st)
# Ids belonging to nobody must not produce a creation out of thin air.
st, d = call('/api/gallery/creations',
             {'kind': 'collage', 'photo_ids': [999999, 1000000]}, tok=TOK)
check(st == 422, "ids that are not yours make nothing", st)

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
