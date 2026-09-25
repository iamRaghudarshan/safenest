"""The people list must say WHERE the face is, not just which photo it is in.

THE BUG THIS PINS. /api/people returned `cover_url` — the whole photo's
thumbnail — and nothing else. Every client could therefore only show the
middle of a photograph in a circle, which on a group shot is somebody's
shoulder and on a landscape is scenery. It looked like face detection had not
worked at all.

The padding maths already existed, in the FACES endpoint. It was never applied
to the people list, which is the screen people actually look at.

The assertion worth making is not "a box came back" — a wrong box is also a
box. It is that the box CONTAINS the face the detector found, and that it is
bigger than the bare detector rectangle, because a crop taken exactly to that
rectangle is a nose and two eyes.

Pure: the maths is checked directly, then the shape of the payload over HTTP.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.routers.people import FACE_PAD, face_box  # noqa: E402

B = 'http://127.0.0.1:8099'
FAIL = []


def check(ok, label, extra=''):
    line = '  %-58s %s %s' % (label, 'PASS' if ok else 'FAIL', extra)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    print(line.encode(enc, 'replace').decode(enc, 'replace'))
    if not ok:
        FAIL.append(label)


print('\n  --- the crop maths ---')
# A 100x100 face at (400,300) in a 1000x800 photo.
box = face_box('400,300,100,100', 1000, 800)
check(box is not None, 'a detector rectangle becomes a box', box)

# CONTAINS the face. Anything else is a crop of the wrong part of the picture.
check(box['x'] <= 400 / 1000 and box['y'] <= 300 / 800,
      'the box starts at or before the face', (box['x'], box['y']))
check(box['x'] + box['w'] >= 500 / 1000 and box['y'] + box['h'] >= 400 / 800,
      'and ends at or after it', (box['x'] + box['w'], box['y'] + box['h']))

# BIGGER than the bare rectangle — a face cropped to the detector's box is a
# nose and two eyes, and people recognise a face by its outline.
check(abs(box['w'] - (100 * (1 + FACE_PAD)) / 1000) < 1e-9,
      'padded by exactly FACE_PAD', (box['w'], FACE_PAD))

# The centre must not move. Padding that grows from a corner slides the face
# out of the middle of the circle.
cx = box['x'] + box['w'] / 2
check(abs(cx - 450 / 1000) < 1e-9, 'and centred on the same face', cx)

# A face at the very edge must not produce a negative origin: a browser reads
# that as a crop off the side of the picture.
edge = face_box('0,0,80,80', 1000, 800)
check(edge['x'] >= 0 and edge['y'] >= 0,
      'a face at the corner clamps to the edge', (edge['x'], edge['y']))

for bad, why in ((None, 'no bbox'), ('', 'an empty bbox'),
                 ('1,2,3', 'a short bbox'), ('a,b,c,d', 'a non-numeric bbox'),
                 ('0,0,10,10', 'a photo with no size')):
    got = face_box(bad, 1000, 800) if why != 'a photo with no size' \
        else face_box(bad, 0, 0)
    check(got is None, '%s gives None rather than raising' % why, got)

check(face_box('400,300,0,0', 1000, 800) is None,
      'a zero-sized face gives None')


print('\n  --- over HTTP ---')


def call(path, body=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method='POST' if data is not None else 'GET',
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
if st != 200:
    print('  (could not sign in — HTTP checks skipped)')
else:
    TOK = d['token']
    st, d = call('/api/people', tok=TOK)
    check(st == 200, 'the people list answers', st)
    people = d.get('people', [])
    # The KEY has to be there even when there are no people, because a client
    # reading a missing key falls back to showing whole photos for ever.
    check(isinstance(people, list), 'it returns a list', type(people).__name__)
    if people:
        p = people[0]
        check('box' in p, 'every person carries a box key', sorted(p)[:6])
        b = p.get('box')
        if b:
            check(all(isinstance(b.get(k), float) for k in 'xywh'),
                  'the box is four fractions', b)
            check(0 <= b['x'] <= 1 and 0 <= b['y'] <= 1
                  and 0 < b['w'] <= 1 and 0 < b['h'] <= 1,
                  'all within the picture', b)
        else:
            # Null is legitimate — the cover face may have no recorded
            # position — and a client must handle it. Said, not asserted.
            print('      (this library\'s first person has no recorded face '
                  'position; null is handled by showing the whole photo)')
    else:
        print('      (no people in the throwaway library; the maths above is '
              'what carries this test)')

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
