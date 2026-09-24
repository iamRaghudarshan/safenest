"""Editing a photo, and never losing the one that was taken.

The feature is crop / rotate / adjust / filter. The things that actually go
wrong are none of those:

  * `content_hash` must NOT change. It is the identity of the upload — the
    phone's backup asks "which of these do you already have?" by it before
    sending a byte. Recompute it on edit and every phone re-sends its entire
    camera roll.
  * `width`/`height` MUST change, because the justified timeline lays rows out
    from them and a cropped photo reporting its old shape jumps.
  * the pristine original must survive, be reachable, and be removed when the
    photo is — the same leak that document versions had.
  * editing twice must crop the ORIGINAL twice, not crop a crop.

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

#: Read from the module under test rather than typed again here: a test that
#: hard-codes ".orig" keeps passing after somebody renames the suffix.
sys.path.insert(0, __file__.rsplit('verify_', 1)[0])
from app.photoedit import PRISTINE_SUFFIX as photoedit_suffix  # noqa: E402
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
    req = urllib.request.Request(B + '/api/gallery/upload', data=bytes(out),
                                 method='POST',
                                 headers={'Content-Type':
                                          'multipart/form-data; boundary=' + b,
                                          'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, json.load(r)


def fetch(url, tok):
    req = urllib.request.Request(
        url if url.startswith('http') else B + url,
        headers={'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
assert st == 200, (st, d)
TOK = d['token']
print('  signed in')

# A wide picture with a distinctly coloured top-left corner, so a crop can be
# checked by looking at what came back rather than only at its size.
im = Image.new('RGB', (400, 200), (30, 90, 200))
for x in range(100):
    for y in range(50):
        im.putpixel((x, y), (240, 30, 30))
buf = io.BytesIO()
im.save(buf, 'JPEG', quality=95)
st, d = upload('edit-me-%s.jpg' % uuid.uuid4().hex[:6], buf.getvalue(), TOK)
assert st == 200, (st, d)
PID = (d.get('item') or d)['id']
before = (d.get('item') or d)
print('  photo id =', PID, '%dx%d' % (before.get('width') or 0, before.get('height') or 0))

def hashes_of(pid):
    """content_hash and source_hash, read from the DATABASE.

    Neither is in the API response — _present does not carry them, and the
    first version of this test compared `None` to `None` and passed. That is
    the single most important assertion here (a changed content_hash means
    every phone re-uploads its camera roll) and it was proving nothing at all.
    """
    con = sqlite3.connect(DB)
    try:
        row = con.execute(
            "SELECT content_hash, source_hash FROM gallery_photos WHERE id = ?",
            (pid,)).fetchone()
    finally:
        con.close()
    return (row or (None, None))


HASH_BEFORE, SOURCE_BEFORE = hashes_of(PID)
assert HASH_BEFORE, "the photo has no content_hash; the rest proves nothing"


print('\n  --- rotate ---')
st, d = call('/api/gallery/%d/edit' % PID, {'rotate': 90}, tok=TOK)
check(st == 200, 'a rotate is accepted', st)
item = d.get('item', {})
check((item.get('width'), item.get('height')) == (200, 400),
      'a 90 turn swaps the stored dimensions',
      (item.get('width'), item.get('height')))
check(d.get('edit_text') == 'rotated 90°', 'and says what it did', d.get('edit_text'))
check(item.get('edit') == {'rotate': 90},
      'the edit travels back so the editor can reopen where it was left',
      item.get('edit'))


print('\n  --- crop ---')
# Top-left quarter — the red corner.
st, d = call('/api/gallery/%d/edit' % PID,
             {'crop': {'x': 0, 'y': 0, 'w': 0.25, 'h': 0.25}}, tok=TOK)
item = d.get('item', {})
check((item.get('width'), item.get('height')) == (100, 50),
      'a quarter crop of a 400x200 gives 100x50',
      (item.get('width'), item.get('height')))

# The media URL is signed and comes back on the item itself; there is no
# /file route on a photo, and reaching for one returned 404 and looked like a
# broken crop.
got = Image.open(io.BytesIO(fetch(item['url'], TOK))).convert('RGB')
check(got.size == (100, 50), 'and the FILE is that size too, not just the row', got.size)
r, g, b = got.getpixel((50, 25))
check(r > 180 and g < 90 and b < 90,
      'the crop kept the red corner, not the blue rest', (r, g, b))

# THE ONE THAT MATTERS MOST. A second crop must cut the ORIGINAL again, not
# cut the already-cropped result — otherwise every edit compounds and the
# photo shrinks towards nothing.
st, d = call('/api/gallery/%d/edit' % PID,
             {'crop': {'x': 0, 'y': 0, 'w': 0.5, 'h': 0.5}}, tok=TOK)
item = d.get('item', {})
check((item.get('width'), item.get('height')) == (200, 100),
      'a SECOND crop re-cuts the original, it does not crop the crop',
      (item.get('width'), item.get('height')))


print('\n  --- what must not move ---')
now_content, now_source = hashes_of(PID)
check(now_content == HASH_BEFORE,
      'content_hash is unchanged — the phone still recognises its upload',
      (HASH_BEFORE[:12], (now_content or '?')[:12]))
check(now_source == SOURCE_BEFORE, 'and so is source_hash',
      ((SOURCE_BEFORE or '?')[:12], (now_source or '?')[:12]))

# /have is the pre-flight the phone uses before sending a byte. After three
# edits it must still say "yes, I have that one".
st, d = call('/api/gallery/have', {'hashes': [SOURCE_BEFORE]}, tok=TOK)
check(d.get('have') == [SOURCE_BEFORE],
      'and the backup pre-flight still matches it', d.get('have'))


print('\n  --- filters and adjustments ---')
st, d = call('/api/gallery/%d/edit' % PID, {'filter': 'mono'}, tok=TOK)
check(st == 200 and d.get('edit_text') == 'mono', 'a filter applies', d.get('edit_text'))
got = Image.open(io.BytesIO(fetch(d['item']['url'], TOK))).convert('RGB')
r, g, b = got.getpixel((10, 10))
check(abs(r - g) < 12 and abs(g - b) < 12,
      'and mono really is grey, not merely labelled', (r, g, b))

st, d = call('/api/gallery/%d/edit' % PID,
             {'brightness': 1.4, 'contrast': 1.2, 'filter': 'sepia'}, tok=TOK)
check(st == 200, 'adjustments combine with a filter', st)
check('brightness 1.4' in (d.get('edit_text') or ''),
      'and the description names them', d.get('edit_text'))

st, d = call('/api/gallery/%d/edit' % PID, {'brightness': 99}, tok=TOK)
check(st == 200 and (d.get('item', {}).get('edit') or {}).get('brightness') == 2.0,
      'an out-of-range slider clamps rather than failing',
      (d.get('item', {}).get('edit') or {}).get('brightness'))


print('\n  --- refusals ---')
for bad, why in (({'rotate': 45}, 'a rotation that is not a quarter turn'),
                 ({'crop': {'x': 0.8, 'y': 0, 'w': 0.5, 'h': 0.5}},
                  'a crop that falls off the edge'),
                 ({'crop': {'x': 0, 'y': 0, 'w': 0.001, 'h': 0.001}},
                  'a crop too small to keep'),
                 ({'filter': 'instagram'}, 'a filter that does not exist')):
    st, d = call('/api/gallery/%d/edit' % PID, bad, tok=TOK)
    check(st == 422, why + ' is refused', st)

st, d = call('/api/gallery/999999/edit', {'rotate': 90}, tok=TOK)
check(st == 404, "another account's photo is a 404", st)


print('\n  --- revert ---')
st, d = call('/api/gallery/%d/edit/revert' % PID, {}, tok=TOK)
check(st == 200, 'revert is accepted', st)
item = d.get('item', {})
check((item.get('width'), item.get('height')) == (400, 200),
      'the photo is its original shape again',
      (item.get('width'), item.get('height')))
check(item.get('edit') in (None, {}), 'and carries no edit', item.get('edit'))

got = Image.open(io.BytesIO(fetch(item['url'], TOK))).convert('RGB')
check(got.size == (400, 200), 'the FILE is the original again', got.size)
r, g, b = got.getpixel((300, 150))
check(b > 150 and r < 90, 'including the blue that a crop had thrown away', (r, g, b))

st, d = call('/api/gallery/%d/edit/revert' % PID, {}, tok=TOK)
check(st == 200, 'reverting an unedited photo is a no-op, not an error', st)

# Dragging every slider back to the middle is a revert, not an error.
st, d = call('/api/gallery/%d/edit' % PID, {'rotate': 0, 'filter': 'none'}, tok=TOK)
check(st == 200 and (d.get('item', {}).get('edit') in (None, {})),
      'an edit of nothing reverts rather than failing', d.get('edit_text'))


print('\n  --- reconcile must not call the original stray ---')
st, d = call('/api/gallery/%d/edit' % PID, {'rotate': 180}, tok=TOK)
assert st == 200, d
st, rec = call('/api/system/reconcile', tok=TOK)
if st == 200:
    g = rec.get('gallery', {})
    blob = json.dumps(rec)
    # Scoped to THIS photo, not to the whole library. The throwaway database
    # carries debris from every other script that ran against it — rows whose
    # files another test deleted — and failing on those would make this test
    # report a fault in a feature it is not testing.
    check(photoedit_suffix not in blob,
          'no pristine copy is reported as an orphan file',
          [n for n in (g.get('samples') or {}).get('stray_file', [])
           if photoedit_suffix in n][:3])
    mine = [m for m in (g.get('samples') or {}).get('missing_file', [])
            if m.get('id') == PID]
    check(not mine, 'and this photo is not reported as missing its file', mine)
else:
    check(False, 'reconcile is reachable', st)


print('\n  --- deleting takes the original with it ---')
st, d = call('/api/gallery/%d' % PID, method='DELETE', tok=TOK)
check(st == 200, 'the edited photo bins', st)
st, d = call('/api/gallery/trash/empty', {}, tok=TOK)
check(st == 200, 'and the bin empties', st)
st, rec = call('/api/system/reconcile', tok=TOK)
if st == 200:
    left = [n for n in ((rec.get('gallery', {}) or {}).get('samples')
                        or {}).get('stray_file', [])
            if photoedit_suffix in n]
    check(not left,
          'no pristine copy is left behind — not the versions leak again',
          left[:3])

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
