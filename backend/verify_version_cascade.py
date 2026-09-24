"""Deleting a document takes its versions with it.

FOUND BY A TEST SUITE, NOT BY READING THE CODE. verify_drive.py started
reporting "a fresh document has no versions: 10" — a brand-new upload that
already had ten earlier copies. The cause was that permanent delete removed
the document row and its current file and left document_versions behind, and
ids get reused: the next document to take that id inherited a deleted
document's history.

That is not untidiness. It means paperwork somebody deleted is listed inside
an unrelated file, and Restore will make one of those old copies current.

Runs against the throwaway instance on 8099.
"""
import json
import sys
import urllib.error
import urllib.request
import uuid

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
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            return e.code, {}


def upload(path, fields, filename, content, tok):
    b = '----sn' + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (b, k, v)).encode()
    out += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n' % (b, filename)).encode()
    out += content + ('\r\n--%s--\r\n' % b).encode()
    req = urllib.request.Request(B + path, data=bytes(out), method='POST', headers={
        'Content-Type': 'multipart/form-data; boundary=' + b,
        'Authorization': 'Bearer ' + tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status, json.load(r)


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


st, d = upload('/api/documents', {'title': 'Lease', 'category': 'property'},
               'lease.pdf', pdf('the original'), TOK)
DID = (d.get('item') or d)['id']
for i in range(2, 6):
    upload('/api/documents/%d/replace' % DID, {'note': 'v%d' % i},
           'v%d.pdf' % i, pdf('revision %d' % i), TOK)

st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
before = len(v.get('items', []))
check(before == 4, 'the document has four earlier versions', before)

# Bin it and empty the bin — the full journey, not just the row delete.
call('/api/documents/%d' % DID, method='DELETE', tok=TOK)
st, d = call('/api/documents/trash/empty', {}, tok=TOK)
check(st == 200, 'the bin empties', st)

st, v = call('/api/documents/%d/versions' % DID, tok=TOK)
check(st == 404 or not v.get('items'),
      'the deleted document has no versions left', (st, len(v.get('items', []))))

# THE ONE THAT MATTERS. A new document that takes the same id must start with
# a clean history — not the deleted one's.
st, d = upload('/api/documents', {'title': 'Insurance', 'category': 'insurance'},
               'ins.pdf', pdf('something else entirely'), TOK)
NEW = (d.get('item') or d)['id']
st, v = call('/api/documents/%d/versions' % NEW, tok=TOK)
check(len(v.get('items', [])) == 0,
      'a NEW document starts with no versions, even reusing an id',
      (NEW, DID, len(v.get('items', []))))

# And nothing of the old one is restorable into it.
st, d = call('/api/documents/%d/versions/2/restore' % NEW, {}, tok=TOK)
check(st == 404, "a deleted document's version cannot be restored into a new one", st)
st, d = call('/api/documents/%d/versions/2/file' % NEW, tok=TOK)
check(st == 404, 'nor downloaded from it', st)

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
