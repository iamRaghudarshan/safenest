"""Filter documents by type and by date.

The trap in a type filter is the group nobody names: a file whose extension is
not in any list, or which has no extension at all. `NOT IN` misses NULL in SQL,
so the obvious "other" implementation silently excludes exactly the files it
was written to find — and it looks correct until somebody uploads something
unusual, which is the only time anyone uses that chip.

The trap in a date filter is a half-typed date. A date input sends "2026-0"
while somebody is still typing it, and a filter that 400s on that makes the
whole screen flash an error between keystrokes.

Runs against the throwaway instance on 8099.
"""
import json
import urllib.error
import urllib.request
import uuid

B = 'http://127.0.0.1:8099'
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


def upload(title, filename, content, tok):
    b = '----sn' + uuid.uuid4().hex
    out = bytearray()
    for k, v in (('title', title), ('category', 'other')):
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                % (b, k, v)).encode()
    out += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n' % (b, filename)).encode()
    out += content + ('\r\n--%s--\r\n' % b).encode()
    req = urllib.request.Request(B + '/api/documents', data=bytes(out), method='POST',
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


st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
assert st == 200, (st, d)
TOK = d['token']
print('  signed in')

st, existing = call('/api/documents', tok=TOK)
for x in existing.get('items', []):
    call('/api/documents/%d' % x['id'], method='DELETE', tok=TOK)
call('/api/documents/trash/empty', {}, tok=TOK)

FILES = [
    ('Rent agreement', 'rent.pdf', b'%PDF-1.4\nrent\n%%EOF\n'),
    ('Salary slip', 'slip.pdf', b'%PDF-1.4\nslip\n%%EOF\n'),
    ('Budget', 'budget.csv', b'item,amount\nrent,18000\n'),
    ('Accounts', 'accounts.xlsx', b'PK\x03\x04 not really a workbook'),
    ('Letter', 'letter.docx', b'PK\x03\x04 not really a document'),
    ('Deck', 'deck.pptx', b'PK\x03\x04 not really a deck'),
    ('Backup', 'backup.zip', b'PK\x03\x04 not really an archive'),
    # The one that catches the NULL bug: an extension no group claims.
    ('Firmware', 'router.bin', b'\x00\x01\x02\x03 binary blob'),
]
for title, name, body in FILES:
    st, d = upload(title, name, body, TOK)
    assert st == 200, (name, st, d)
print('  %d documents uploaded' % len(FILES))


def titles(params):
    st, d = call('/api/documents?' + params, tok=TOK)
    return st, sorted(x['title'] for x in d.get('items', []))


print('\n  --- type ---')
st, got = titles('ftype=pdf')
check(got == ['Rent agreement', 'Salary slip'], 'PDFs', got)
st, got = titles('ftype=sheet')
check(got == ['Accounts', 'Budget'], 'spreadsheets include csv and xlsx', got)
st, got = titles('ftype=doc')
check(got == ['Letter'], 'documents', got)
st, got = titles('ftype=slides')
check(got == ['Deck'], 'slides', got)
st, got = titles('ftype=archive')
check(got == ['Backup'], 'archives', got)

# THE ONE THAT MATTERS. .bin is in no group, so it must fall into "other" —
# and it is the case a NOT IN written without a NULL branch gets wrong.
st, got = titles('ftype=other')
check(got == ['Firmware'], 'other is everything no group claimed', got)

st, got = titles('ftype=nonsense')
check(len(got) == len(FILES),
      'an unknown type filters nothing rather than erroring', len(got))

print('\n  --- date ---')
st, all_docs = call('/api/documents', tok=TOK)
today = (all_docs['items'][0].get('created_at') or '')[:10]
check(bool(today), 'documents carry a created date', today)

st, got = titles('since=' + today)
check(len(got) == len(FILES), 'since today finds everything uploaded today', len(got))
st, got = titles('until=' + today)
check(len(got) == len(FILES), 'until today too', len(got))
st, got = titles('since=2099-01-01')
check(got == [], 'a future start date finds nothing', got)
st, got = titles('until=2000-01-01')
check(got == [], 'an ancient end date finds nothing', got)

# A date input sends this between keystrokes.
for half in ('2026-0', '', 'yesterday', '2026-13-45'):
    st, got = titles('since=' + half)
    check(st == 200 and len(got) == len(FILES),
          'a half-typed date %r filters nothing and does not error' % half,
          (st, len(got)))

print('\n  --- together ---')
st, got = titles('ftype=pdf&since=%s&until=%s' % (today, today))
check(got == ['Rent agreement', 'Salary slip'], 'type and date combine', got)
st, got = titles('ftype=pdf&q=Rent')
check(got == ['Rent agreement'], 'type narrows a search rather than replacing it', got)

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
