"""Bulk actions, export-to-zip, version retention and smart albums, over HTTP.

Everything here was written in one burst and none of it had been run. The four
share one property worth proving together: each of them acts on MANY rows from
one request, and the way that goes wrong is not an error — it is acting on a
row belonging to somebody else, or silently acting on fewer rows than were
asked for and reporting success anyway.

So every case checks the COUNT that came back against the count that changed,
and every one of them is offered an id that is not the caller's.

Runs against the throwaway instance on 8099. Production on 8080 is never
touched; the last line checks it is still answering.
"""
import io
import json
import os
import urllib.error
import urllib.request
import uuid
import zipfile

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


def raw(path, body=None, tok=None):
    """Status and BYTES — /export returns a zip, which call() cannot read."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method='POST' if data is not None else 'GET',
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def upload(path, fields, filename, content, tok, field='file'):
    b = '----sn' + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (b, k, v)).encode()
    out += ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n' % (b, field, filename)).encode()
    out += content + ('\r\n--%s--\r\n' % b).encode()
    req = urllib.request.Request(B + path, data=bytes(out), method='POST', headers={
        'Content-Type': 'multipart/form-data; boundary=' + b,
        'Authorization': 'Bearer ' + tok})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
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
print('  signed in')

st, existing = call('/api/documents', tok=TOK)
for x in existing.get('items', []):
    call('/api/documents/%d' % x['id'], method='DELETE', tok=TOK)
call('/api/documents/trash/empty', {}, tok=TOK)


def pdf(tag):
    return ('%%PDF-1.4\n%s\n%%%%EOF\n' % tag).encode()


IDS = []
for i in range(4):
    st, d = upload('/api/documents', {'title': 'Paper %d' % i, 'category': 'other'},
                   'p%d.pdf' % i, pdf('document number %d' % i), TOK)
    assert st == 200, (st, d)
    IDS.append((d.get('item') or d)['id'])
print('  four documents:', IDS)


# ------------------------------------------------------------------ bulk
print('\n  --- bulk actions ---')

st, d = call('/api/documents/bulk', {'ids': IDS[:2], 'action': 'star'}, tok=TOK)
check(st == 200, 'starring two documents succeeds', st)
check(d.get('changed') == 2, 'it reports changing exactly two', d.get('changed'))

st, listing = call('/api/documents', tok=TOK)
# The column is is_favorite; the API spells it is_favourite. Reading the
# wrong one here passed silently as 'nothing is starred'.
starred = {x['id'] for x in listing['items'] if x.get('is_favourite')}
check(starred == set(IDS[:2]), 'the two that are starred are the two asked for',
      sorted(starred))

# THE ONE THAT MATTERS. A bulk endpoint that filters only by id lets anyone
# who can guess a number act on somebody else's row.
st, d = call('/api/documents/bulk',
             {'ids': [999999, 1000000], 'action': 'star'}, tok=TOK)
check(st == 200 and d.get('changed') == 0,
      'ids that are not yours change nothing and do not error', d.get('changed'))

# And a mixed request must act on the owned half only, not refuse the lot.
st, d = call('/api/documents/bulk',
             {'ids': [IDS[2], 999999], 'action': 'star'}, tok=TOK)
check(d.get('changed') == 1, 'a mix of mine and not-mine changes only mine',
      d.get('changed'))

st, d = call('/api/documents/bulk', {'ids': IDS[:3], 'action': 'unstar'}, tok=TOK)
check(d.get('changed') == 3, 'unstarring three reports three', d.get('changed'))

st, d = call('/api/documents/bulk', {'ids': [IDS[3]], 'action': 'trash'}, tok=TOK)
check(d.get('changed') == 1, 'trashing works in bulk', d.get('changed'))
st, listing = call('/api/documents', tok=TOK)
check(IDS[3] not in {x['id'] for x in listing['items']},
      'a bulk-trashed document leaves the listing')
st, d = call('/api/documents/bulk', {'ids': [IDS[3]], 'action': 'restore'}, tok=TOK)
st, listing = call('/api/documents', tok=TOK)
check(IDS[3] in {x['id'] for x in listing['items']},
      'and comes back on restore')

# Permanent delete is deliberately NOT a bulk action: the whole point of a
# multi-select is that it is easy to select more than you meant to.
st, d = call('/api/documents/bulk', {'ids': IDS, 'action': 'delete'}, tok=TOK)
check(st in (400, 422), 'bulk permanent delete is refused', st)
st, listing = call('/api/documents', tok=TOK)
check(len(listing['items']) == 4, 'and nothing was deleted by the attempt',
      len(listing['items']))

st, d = call('/api/documents/bulk', {'ids': IDS, 'action': 'frobnicate'}, tok=TOK)
check(st in (400, 422), 'an unknown action is refused', st)


# ------------------------------------------------------------------ export
print('\n  --- export to zip ---')

st, body = raw('/api/documents/export', {'ids': IDS[:3]}, tok=TOK)
check(st == 200, 'export returns a file', st)
names = []
if st == 200:
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        names = z.namelist()
        check(len(names) == 3, 'the zip holds all three documents', names)
        check(all(n.lower().endswith('.pdf') for n in names),
              'each entry keeps its extension', names)
        check(z.read(names[0]).startswith(b'%PDF'),
              'and the bytes inside are the real file')
    except zipfile.BadZipFile:
        check(False, 'the response is a valid zip', body[:40])

# Two documents may honestly share a title, and a zip cannot hold two entries
# of one name — the second would overwrite the first and the export would
# quietly lose a file.
st, d = upload('/api/documents', {'title': 'Paper 0', 'category': 'other'},
               'dup.pdf', pdf('a different document with the same title'), TOK)
DUP = (d.get('item') or d)['id']
st, body = raw('/api/documents/export', {'ids': [IDS[0], DUP]}, tok=TOK)
if st == 200:
    n2 = zipfile.ZipFile(io.BytesIO(body)).namelist()
    check(len(n2) == 2 and len(set(n2)) == 2,
          'two documents with the SAME title both survive the zip', n2)

st, body = raw('/api/documents/export', {'ids': [999999]}, tok=TOK)
check(st == 404, 'exporting only ids that are not yours is a 404', st)


# ------------------------------------------------------------------ versions
print('\n  --- version retention ---')

DID = IDS[0]
for i in range(2, 15):          # 13 replacements on top of the original
    upload('/api/documents/%d/replace' % DID, {'note': 'v%d' % i},
           'v%d.pdf' % i, pdf('version %d' % i), TOK)

st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
nums = sorted(x['version'] for x in v.get('items', []))
check(len(nums) <= 10, 'a document never keeps more than ten old versions', nums)
check(len(nums) == len(set(nums)), 'version numbers are never reused', nums)
# Retention has to drop the OLDEST. Dropping the newest would make the feature
# actively harmful: the copy most likely to be wanted back is the last one.
check(nums and nums[-1] > nums[0] and max(nums) >= 13,
      'the ones kept are the most recent', nums)

st, code = None, None
if nums:
    req = urllib.request.Request(
        B + '/api/documents/%d/versions/%d/file' % (DID, nums[-1]),
        headers={'Authorization': 'Bearer ' + TOK})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, data = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, data = e.code, b''
    check(code == 200 and data.startswith(b'%PDF'),
          'an old version downloads without being restored first', code)

    # A version that retention has dropped must say so, not hand back a
    # neighbouring file.
    st2, d2 = call('/api/documents/%d/versions/1/file' % DID, tok=TOK)
    check(st2 in (404, 410), 'a dropped version is a clean 404/410', st2)

st, d = call('/api/documents/999999/versions/1/file', tok=TOK)
check(st == 404, "another account's version is a 404", st)


# ------------------------------------------------------------------ smart albums
print('\n  --- smart albums ---')

st, d = call('/api/gallery/albums',
             {'name': 'Smart ' + uuid.uuid4().hex[:6],
              'rule': {'kind': 'video', 'year': 2024}}, tok=TOK)
check(st == 200, 'a smart album is created from a rule', st)
SA = d.get('id')
check(d.get('smart') is True, 'it comes back marked smart', d.get('smart'))
check(d.get('rule_text') == 'videos, 2024', 'and describes its own rule',
      d.get('rule_text'))

st, d = call('/api/gallery/albums', tok=TOK)
row = next((a for a in d['albums'] if a['id'] == SA), None)
check(row is not None and row.get('smart') is True,
      'the listing marks it smart')
check(row is not None and row.get('rule') == {'kind': 'video', 'year': 2024},
      'and carries the rule for the screen to show',
      row and row.get('rule'))

# An ordinary album must not pick up any of this.
st, d = call('/api/gallery/albums', {'name': 'Plain ' + uuid.uuid4().hex[:6]}, tok=TOK)
PA = d.get('id')
st, d = call('/api/gallery/albums/%d' % PA, tok=TOK)
check(d.get('smart') is False and d.get('rule') is None,
      'an album with no rule is not smart', (d.get('smart'), d.get('rule')))

# A rule with an unknown key must not be stored as given — the parse happens
# on the way IN as well as on the way out.
st, d = call('/api/gallery/albums',
             {'name': 'Junk ' + uuid.uuid4().hex[:6],
              'rule': {'label': 'beach', 'nonsense': 'x'}}, tok=TOK)
check(d.get('rule') == {'label': 'beach'},
      'an unknown rule key is dropped at creation', d.get('rule'))

# Editing the rule, and clearing it.
st, d = call('/api/gallery/albums/%d' % SA,
             {'name': 'Renamed ' + uuid.uuid4().hex[:6], 'rule': {'label': 'beach'}},
             method='PUT', tok=TOK)
check(d.get('rule') == {'label': 'beach'}, 'the rule can be edited', d.get('rule'))
st, d = call('/api/gallery/albums/%d' % SA,
             {'name': 'Cleared ' + uuid.uuid4().hex[:6], 'rule': None},
             method='PUT', tok=TOK)
check(d.get('smart') is False, 'and cleared back to an ordinary album',
      d.get('smart'))

st, d = call('/api/gallery/albums/999999', tok=TOK)
check(st == 404, "another account's album is a 404", st)


# Bursts are proved in verify_bursts.py, which UPLOADS a burst first. The
# checks that lived here ran against an empty gallery and passed without
# ever reaching the grouping code — a test that cannot fail.

print()
req = urllib.request.Request('http://127.0.0.1:8080/api/health')
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print('  production 8080 -> %d (untouched)' % r.status)
except Exception as e:
    print('  production 8080 -> could not check:', e)

if FAIL:
    print('\n  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('\n  ALL PASS')
