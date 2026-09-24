"""Drawing on a photo, and the one mark that must really delete something.

Markup is mostly decoration and mostly cannot go badly wrong. One tool can:
**redact**. The reason a household app needs markup at all is usually covering
an account number before sending a photo of a bill to somebody, and a black
rectangle DRAWN OVER the pixels is undone by anyone who opens the file in
another editor. So the test reads the pixels back and insists they are gone.

The second property worth pinning is that markup is part of the EDIT, not a
separate stamp on the file: it re-renders from the pristine original like
everything else, which is what lets "Use original" bring back a photo somebody
redacted. That is a deliberate trade and it is worth stating — the redaction
is permanent in the shared copy, not in the library.

Pure where it can be, over HTTP where the round trip is the point.
"""
import io
import json
import sys
import urllib.error
import urllib.request
import uuid

from PIL import Image

sys.path.insert(0, __file__.rsplit('verify_', 1)[0])
from app import photoedit as pe  # noqa: E402

B = 'http://127.0.0.1:8099'
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


print('\n  --- what a mark may be ---')
e = pe.normalise({'markup': [
    {'t': 'pen', 'c': 'red', 'p': [[0.1, 0.1], [0.5, 0.5]]},
    {'t': 'text', 'c': 'white', 'x': 0.2, 'y': 0.2, 'text': 'hello'},
]})
check(len(e.get('markup', [])) == 2, 'two good marks survive', len(e.get('markup', [])))

# A tap is how somebody puts a tool down, not a stroke.
e = pe.normalise({'markup': [{'t': 'pen', 'c': 'red', 'p': [[0.5, 0.5]]}]})
check('markup' not in e, 'a single point is not a stroke', e)

e = pe.normalise({'markup': [{'t': 'text', 'c': 'red', 'x': 0.1, 'y': 0.1,
                              'text': '   '}]})
check('markup' not in e, 'an empty label is not a mark', e)

for bad, why in (({'markup': [{'t': 'spray', 'p': [[0, 0], [1, 1]]}]},
                  'an unknown tool'),
                 ({'markup': [{'t': 'pen', 'c': 'puce', 'p': [[0, 0], [1, 1]]}]},
                  'an unknown colour'),
                 ({'markup': 'lots'}, 'markup that is not a list')):
    try:
        pe.normalise(bad)
        check(False, why + ' is refused')
    except pe.EditError as exc:
        check(True, why + ' is refused', str(exc)[:44])

# Points outside the picture are clamped, not refused: dragging past the edge
# is a normal gesture and losing the whole stroke over it would be worse.
e = pe.normalise({'markup': [{'t': 'pen', 'c': 'red',
                              'p': [[-3, 0.5], [9, 0.5]]}]})
check(e['markup'][0]['p'] == [[0.0, 0.5], [1.0, 0.5]],
      'a stroke dragged off the edge is clamped, not lost', e['markup'][0]['p'])

# The cap exists so one row cannot hold a novel.
many = pe.normalise({'markup': [
    {'t': 'pen', 'c': 'red', 'p': [[0, 0], [1, 1]]}] * 200})
check(len(many['markup']) == pe.MAX_MARKUP_OPS, 'the number of marks is capped',
      len(many['markup']))

check(pe.describe(pe.normalise({'markup': [
    {'t': 'redact', 'p': [[0.1, 0.1], [0.5, 0.5]]},
    {'t': 'pen', 'c': 'red', 'p': [[0, 0], [1, 1]]}]})) == '2 marks (1 redacted)',
      'the description names the redaction separately')


print('\n  --- redaction really deletes ---')
st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
assert st == 200, (st, d)
TOK = d['token']

# A photo with an unmistakable block to cover.
im = Image.new('RGB', (600, 400), (30, 90, 200))
for y in range(40, 160):
    for x in range(300, 560):
        im.putpixel((x, y), (250, 30, 30))
buf = io.BytesIO()
im.save(buf, 'JPEG', quality=95)

b = '----sn' + uuid.uuid4().hex
out = bytearray()
out += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="mk-%s.jpg"'
        '\r\nContent-Type: image/jpeg\r\n\r\n' % (b, uuid.uuid4().hex[:6])).encode()
out += buf.getvalue() + ('\r\n--%s--\r\n' % b).encode()
req = urllib.request.Request(B + '/api/gallery/upload', data=bytes(out),
                             method='POST',
                             headers={'Content-Type':
                                      'multipart/form-data; boundary=' + b,
                                      'Authorization': 'Bearer ' + TOK})
with urllib.request.urlopen(req, timeout=120) as r:
    PID = (json.load(r).get('item') or {})['id']

st, d = call('/api/gallery/%d/edit' % PID, {'markup': [
    # Covers the LEFT half of the red block only, so the right half can be
    # checked as untouched. The first version redacted the whole block, and
    # the "outside" probe then landed on the background and read as a failure.
    {'t': 'redact', 'c': 'black', 'p': [[0.48, 0.08], [0.72, 0.42]]},
    {'t': 'arrow', 'c': 'blue', 'w': 0.012, 'p': [[0.1, 0.8], [0.45, 0.5]]},
    {'t': 'text', 'c': 'white', 'w': 0.025, 'x': 0.06, 'y': 0.06,
     'text': 'Rent receipt'},
]}, tok=TOK)
check(st == 200, 'marks are accepted', st)
check('3 marks (1 redacted)' in (d.get('edit_text') or ''),
      'and reported', d.get('edit_text'))

got = Image.open(io.BytesIO(fetch(d['item']['url'], TOK))).convert('RGB')
r, g, bl = got.getpixel((int(0.6 * got.width), int(0.25 * got.height)))
# THE ASSERTION THE WHOLE TOOL EXISTS FOR.
check(r < 80 and g < 80 and bl < 80,
      'the redacted area is dark — the pixels are gone, not covered', (r, g, bl))

r2, g2, b2 = got.getpixel((int(0.85 * got.width), int(0.25 * got.height)))
check(r2 > 150 and g2 < 110,
      'and the red outside the box is untouched', (r2, g2, b2))

# Something was drawn where the arrow goes: the photo is flat blue there, so
# any pixel that is not blue is ink.
r3, g3, b3 = got.getpixel((int(0.28 * got.width), int(0.65 * got.height)))
# `or True` was here and made this always pass — a check that cannot fail.
# The background is (30, 90, 200) and the blue ink is (30, 136, 229), so the
# green channel is what tells them apart.
check(g3 > 115, 'the arrow is really drawn, not just accepted', (r3, g3, b3))


print('\n  --- markup is part of the edit ---')
st, listing = call('/api/gallery?limit=200', tok=TOK)
row = next((x for x in listing['items'] if x['id'] == PID), {})
check(len((row.get('edit') or {}).get('markup') or []) == 3,
      'the marks come back with the photo, so the editor can reopen them',
      row.get('edit'))

# Redaction is permanent in the RENDERED photo, and the original is still
# there. That is the deliberate trade, and it has to actually hold.
st, d = call('/api/gallery/%d/edit/revert' % PID, {}, tok=TOK)
check(st == 200, 'the photo reverts', st)
got = Image.open(io.BytesIO(fetch(d['item']['url'], TOK))).convert('RGB')
r, g, bl = got.getpixel((int(0.6 * got.width), int(0.25 * got.height)))
check(r > 150 and g < 110,
      'and "Use original" brings back what the redaction covered', (r, g, bl))

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
