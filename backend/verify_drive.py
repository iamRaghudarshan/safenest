"""Copy, Recent and Versions — section 28's missing operations.

Real uploads over HTTP against a throwaway instance. The property that matters
for versions is that replacing a file NEVER destroys the old one: a wrong scan
uploaded over a right one used to take the right one with it, and what a
document store holds is usually irreplaceable.
"""
import json
import os
import urllib.error
import urllib.request
import uuid

B = 'http://127.0.0.1:8099'
FAIL = []


def check(ok, label, extra=''):
    print('  %-54s %s %s' % (label, 'PASS' if ok else 'FAIL', extra))
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


def fetch_status(path):
    """Status only. /file returns a PDF, not JSON, so call() cannot read it."""
    req = urllib.request.Request(B + path, headers={'Authorization': 'Bearer ' + TOK})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def upload(path, fields, filename, content, tok, field='file'):
    """Minimal multipart, so this needs no HTTP library."""
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

# Start from a clean library so counts are about this run.
st, existing = call('/api/documents', tok=TOK)
for x in existing.get('items', []):
    call('/api/documents/%d' % x['id'], method='DELETE', tok=TOK)
call('/api/documents/trash/empty', {}, tok=TOK)

V1 = b'%PDF-1.4\nVERSION ONE - the correct scan\n%%EOF\n'
V2 = b'%PDF-1.4\nVERSION TWO - uploaded by mistake\n%%EOF\n'
V3 = b'%PDF-1.4\nVERSION THREE - the good one again\n%%EOF\n'

st, d = upload('/api/documents', {'title': 'Rent agreement', 'category': 'property'},
               'rent.pdf', V1, TOK)
check(st == 200, 'a document uploads', st)
doc = (d.get('item') or d)
DID = doc['id']
print('  document id =', DID)

# ---- copy -----------------------------------------------------------------
print()
print('  --- copy ---')
# Unique name per run: the folder unique index correctly refuses a duplicate,
# so a fixed name makes the second run fail on setup rather than on the thing
# under test.
st, d = call('/api/documents/folders',
             {'name': 'Property ' + uuid.uuid4().hex[:6]}, tok=TOK)
assert st == 200, (st, d)
FID = d['item']['id']
st, d = call('/api/documents/%d/copy' % DID, {'folder_id': FID}, tok=TOK)
check(st == 200, 'copy returns the new document', st)
CID = (d.get('item') or {}).get('id')
check(CID and CID != DID, 'the copy is a different document', CID)

st, listing = call('/api/documents?folder=%d' % FID, tok=TOK)
check(any(x['id'] == CID for x in listing.get('items', [])),
      'the copy landed in the chosen folder')

st, all_docs = call('/api/documents', tok=TOK)
check(len(all_docs.get('items', [])) == 2, 'there are now two documents',
      len(all_docs.get('items', [])))

# Deleting one copy must not affect the other — that is what "copy" means.
st, _ = call('/api/documents/%d' % CID, method='DELETE', tok=TOK)
check(fetch_status('/api/documents/%d/file' % DID) == 200,
      'the original still downloads after the copy is binned')

# ---- recent ---------------------------------------------------------------
print()
print('  --- recent ---')
st, rec = call('/api/documents/recent', tok=TOK)
check(st == 200, 'recent responds', st)
check(all(k in rec for k in ('added', 'changed', 'starred')),
      'recent has added / changed / starred', sorted(rec.keys()))
check(any(x['id'] == DID for x in rec['added']), 'the new document is in "added"')

call('/api/documents/%d/favourite' % DID, {}, tok=TOK)
st, rec = call('/api/documents/recent', tok=TOK)
check(any(x['id'] == DID for x in rec['starred']),
      'starring it puts it in "starred"')

# ---- versions -------------------------------------------------------------
print()
print('  --- versions: the rule is that nothing is destroyed ---')
st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
check(st == 200 and v['total'] == 0, 'a fresh document has no versions', v.get('total'))

st, d = upload('/api/documents/%d/replace' % DID, {'note': 'wrong file'},
               'rent-v2.pdf', V2, TOK)
check(st == 200, 'replace accepts a new file', st)
check(d.get('kept_as_version') == 1, 'the outgoing file was kept as version 1', d)

st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
check(v['total'] == 1, 'one version is listed', v['total'])
check(v['items'][0]['note'] == 'wrong file', 'the note was kept')

# Read the raw bytes to prove WHICH file is current.
def current_bytes():
    req = urllib.request.Request(B + '/api/documents/%d/file' % DID,
                                 headers={'Authorization': 'Bearer ' + TOK})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


check(b'VERSION TWO' in current_bytes(), 'the current file is the new one')

print()
print('  --- restore the mistake ---')
st, d = call('/api/documents/%d/versions/1/restore' % DID, {}, tok=TOK)
check(st == 200 and d.get('restored') == 1, 'version 1 is restored', d)
check(b'VERSION ONE' in current_bytes(), 'the ORIGINAL file is current again')
check(d.get('previous_kept_as') == 2,
      'and the file it replaced was itself kept as a version', d)

st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
check(v['total'] == 1, 'restoring consumed v1 and added v2', v['total'])
check(v['items'][0]['version'] == 2, 'the kept one is the file just replaced')

# Replace again, so there are two versions, then check numbering never reuses.
upload('/api/documents/%d/replace' % DID, {'note': 'third'}, 'v3.pdf', V3, TOK)
st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
nums = [x['version'] for x in v['items']]
check(len(nums) == len(set(nums)), 'version numbers are never reused', nums)
check(max(nums) == 3, 'numbering keeps climbing', nums)

print()
print('  --- ownership ---')
st, d = call('/api/documents/999999/versions', tok=TOK)
check(st == 404, 'versions of a document that is not yours is a 404', st)
st, d = call('/api/documents/%d/versions/99/restore' % DID, {}, tok=TOK)
check(st == 404, 'restoring a version that does not exist is a 404', st)

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
