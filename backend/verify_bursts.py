"""Upload a real burst and a real non-burst, then check what the API says.

The pure test proves the grouping RULE. This proves the wiring: that the
photos come back annotated, that the leader carries the group, and — the part
a pure test cannot reach — that the perceptual hashes the server computes for
five frames of one shot are actually close enough to group. A threshold that
is right in theory and wrong against the server's own hash function would pass
every unit test and group nothing in the product.
"""
import io
import json
import os
import random
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta

from PIL import Image

B = 'http://127.0.0.1:8099'
FAIL = []


def check(ok, label, extra=''):
    print('  %-58s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
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


def upload(name, content, tok):
    b = '----sn' + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: image/jpeg\r\n\r\n' % (b, name)).encode()
    out += content + ('\r\n--%s--\r\n' % b).encode()
    req = urllib.request.Request(B + '/api/gallery/upload', data=bytes(out), method='POST',
                                 headers={'Content-Type':
                                          'multipart/form-data; boundary=' + b,
                                          'Authorization': 'Bearer ' + tok})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            return e.code, {}


def jpeg(im, when):
    """Encode with a capture time.

    Written as tag 306 (DateTime) in the 0th IFD rather than DateTimeOriginal,
    because that is what Pillow alone can write — and gallery.py falls back to
    it, so it lands in shot_at the same way.
    """
    ex = im.getexif()
    ex[306] = when.strftime("%Y:%m:%d %H:%M:%S")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92, exif=ex.tobytes())
    return buf.getvalue()


def frame(seed, jitter):
    """One photo. Same scene for the same seed, with a little sensor noise."""
    rnd = random.Random(seed)
    base = [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for _ in range(64)]                      # an 8x8 scene, upscaled
    im = Image.new("RGB", (8, 8))
    im.putdata(base)
    im = im.resize((320, 320), Image.BICUBIC)
    px = im.load()
    n = random.Random(jitter)
    for _ in range(600):                              # noise: changes the BYTES,
        x, y = n.randrange(320), n.randrange(320)     # not the perceptual hash
        r, g, bl = px[x, y]
        px[x, y] = (min(255, r + 3), g, bl)
    return im


st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
assert st == 200, (st, d)
TOK = d['token']

st, lib = call('/api/gallery?limit=500', tok=TOK)
for p in lib.get('items', []):
    call('/api/gallery/%d' % p['id'], method='DELETE', tok=TOK)
call('/api/gallery/trash/empty', {}, tok=TOK)

T = datetime(2024, 5, 1, 12, 0, 0)

# FIVE FRAMES OF ONE SHOT: same scene, two seconds apart.
burst_ids = []
for i in range(5):
    st, d = upload('burst%d.jpg' % i, jpeg(frame(7, 100 + i), T + timedelta(seconds=2 * i)), TOK)
    assert st == 200, (st, d)
    burst_ids.append((d.get('item') or d)['id'])
print('  burst uploaded:', burst_ids)

# THREE DIFFERENT PHOTOS, also seconds apart. The wedding case, for real.
apart_ids = []
for i in range(3):
    st, d = upload('apart%d.jpg' % i,
                   jpeg(frame(200 + i, 900 + i), T + timedelta(minutes=10, seconds=2 * i)), TOK)
    assert st == 200, (st, d)
    apart_ids.append((d.get('item') or d)['id'])
print('  distinct photos uploaded:', apart_ids)

st, d = call('/api/gallery?limit=100&bursts=1&sort=oldest', tok=TOK)
items = {i['id']: i for i in d.get('items', [])}
check(len(items) == 8, 'all eight photos are in the library', len(items))

lead = items.get(burst_ids[0], {})
check(lead.get('burst_count') == 5,
      'the five frames of one shot come back as one burst of five',
      lead.get('burst_count'))
check(set(lead.get('burst_ids') or []) == set(burst_ids),
      'and the burst names exactly those five', lead.get('burst_ids'))
check(all(items.get(i, {}).get('burst_of') == burst_ids[0] for i in burst_ids[1:]),
      'the other four point at the leader')
check('burst_of' not in lead, 'the leader is not a follower of itself')

check(not any('burst_count' in items.get(i, {}) or 'burst_of' in items.get(i, {})
              for i in apart_ids),
      'three DIFFERENT photos seconds apart are left alone',
      [items.get(i, {}).get('burst_count') for i in apart_ids])

st, plain = call('/api/gallery?limit=100&sort=oldest', tok=TOK)
check(len(plain.get('items', [])) == len(d.get('items', [])),
      'the flag never changes how many photos come back')

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
