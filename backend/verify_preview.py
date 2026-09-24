"""Text and CSV previews.

Three things here fail quietly rather than loudly.

A byte-bounded read almost always lands mid-line, and half a row rendered as
if it were a row is a lie about the data — so the cut has to fall on a line
boundary, and the fact that anything was cut has to be REPORTED, or a
truncated preview looks like a truncated file.

A CSV with commas inside quoted fields is the normal case, not the exotic one,
and a parser that splits on ',' tears those rows apart. (The same mistake has
already been made once in this codebase, on Takeout channel titles.)

And a file that is not valid UTF-8 — a spreadsheet exported from a Windows
machine is usually cp1252 — must still preview, rather than 500 on one smart
quote.

Runs against the throwaway instance on 8099.
"""
import io
import json
import sys
import urllib.error
import urllib.request
import uuid

B = 'http://127.0.0.1:8099'
FAIL = []


def check(ok, label, extra=''):
    # The console here is cp1252, and this file deliberately feeds the server
    # bytes that decode to U+FFFD — printing one crashed the run AFTER the
    # check had already passed, which reads as a failure of the thing under
    # test rather than of the print.
    line = '  %-56s %s %s' % (label, 'PASS' if ok else 'FAIL', extra)
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
    with urllib.request.urlopen(req, timeout=180) as r:
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


def put(title, name, content):
    st, d = upload(title, name, content, TOK)
    assert st == 200, (name, st, d)
    return (d.get('item') or d)['id']


print('\n  --- what gets a preview at all ---')
txt = put('Notes', 'notes.txt', b'first line\nsecond line\nthird line\n')
st, d = call('/api/documents/%d/preview' % txt, tok=TOK)
check(st == 200 and d.get('kind') == 'text', 'a .txt previews as text', (st, d.get('kind')))
check((d.get('text') or '').startswith('first line'), 'and its text is the file',
      (d.get('text') or '')[:20])
check(d.get('truncated') is False, 'a small file is not marked truncated',
      d.get('truncated'))

st, listing = call('/api/documents', tok=TOK)
row = next(x for x in listing['items'] if x['id'] == txt)
check(row.get('is_text') is True, 'and the listing flags it as previewable',
      row.get('is_text'))

pdf = put('Scan', 'scan.pdf', b'%PDF-1.4\nnope\n%%EOF\n')
st, d = call('/api/documents/%d/preview' % pdf, tok=TOK)
check(st == 415, 'a PDF has no text preview', st)
st, listing = call('/api/documents', tok=TOK)
row = next(x for x in listing['items'] if x['id'] == pdf)
check(not row.get('is_text'), 'and is not flagged as previewable', row.get('is_text'))

st, d = call('/api/documents/999999/preview', tok=TOK)
check(st == 404, "another account's document is a 404", st)


print('\n  --- csv ---')
csv_id = put('Budget', 'budget.csv',
             b'Item,Amount,Note\nRent,18000,monthly\n"Sharma, Priya",2500,"quoted, comma"\n')
st, d = call('/api/documents/%d/preview' % csv_id, tok=TOK)
check(d.get('kind') == 'csv', 'a .csv previews as rows', d.get('kind'))
rows = d.get('rows') or []
check(rows and rows[0] == ['Item', 'Amount', 'Note'], 'the header row is intact', rows[:1])
# THE ONE THAT MATTERS. A naive split(',') gives five cells here, not three.
check(len(rows) > 2 and rows[2] == ['Sharma, Priya', '2500', 'quoted, comma'],
      'commas INSIDE quoted fields do not split the row',
      rows[2] if len(rows) > 2 else rows)

tsv = put('Tabbed', 'data.tsv', b'a\tb\tc\n1\t2\t3\n')
st, d = call('/api/documents/%d/preview' % tsv, tok=TOK)
check(d.get('rows', [[]])[0] == ['a', 'b', 'c'], 'a .tsv splits on tabs', d.get('rows'))


print('\n  --- bounds ---')
# Bigger than PREVIEW_BYTES, with numbered lines so the cut can be inspected.
big = ''.join('line %06d padding padding padding padding\n' % i for i in range(20000))
big_id = put('Big log', 'big.log', big.encode())
st, d = call('/api/documents/%d/preview' % big_id, tok=TOK)
check(st == 200, 'a large file still previews', st)
check(d.get('truncated') is True, 'and says it was cut', d.get('truncated'))
text = d.get('text') or ''
check(len(text) < len(big), 'only part of it came back', (len(text), len(big)))
# The cut must land on a line boundary: the last line shown has to be whole.
last = text.rstrip('\n').split('\n')[-1]
check(last.startswith('line ') and last.endswith('padding'),
      'the cut falls between lines, not mid-line', repr(last[-30:]))
check(d.get('size_bytes') == len(big), 'the real size is reported', d.get('size_bytes'))

# Many rows, each short: cut by ROW count rather than by bytes.
manyrows = 'a,b\n' + ''.join('%d,%d\n' % (i, i) for i in range(1000))
many_id = put('Many rows', 'many.csv', manyrows.encode())
st, d = call('/api/documents/%d/preview' % many_id, tok=TOK)
check(len(d.get('rows') or []) <= 200, 'a long CSV stops at 200 rows',
      len(d.get('rows') or []))
check(d.get('truncated') is True, 'and says so', d.get('truncated'))


print("\n  --- office files, read not rendered ---")
import zipfile

CT = ('<?xml version="1.0" encoding="UTF-8"?><Types '
      'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SNS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def ooxml(parts):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        for k, v in parts.items():
            z.writestr(k, v)
    return buf.getvalue()


doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="%s"><w:body>'
       '<w:p><w:r><w:t>Rental agreement</w:t></w:r></w:p>'
       '<w:p><w:r><w:t>Signed on </w:t></w:r><w:r><w:t>1 April.</w:t></w:r></w:p>'
       '</w:body></w:document>' % WNS)
did = put('Lease', 'lease.docx', ooxml({'word/document.xml': doc}))
st, d = call('/api/documents/%d/preview' % did, tok=TOK)
check(st == 200 and d.get('kind') == 'doc', 'a .docx previews as a document',
      (st, d.get('kind')))
check((d.get('paragraphs') or [None])[0] == 'Rental agreement',
      'with its text', (d.get('paragraphs') or [])[:1])
check('Signed on 1 April.' in (d.get('paragraphs') or []),
      'and split runs joined back together', d.get('paragraphs'))
# The screen has to SAY it is text only, or a Word file shown as bare
# paragraphs reads as a document that has lost its formatting, and somebody
# goes looking for the version that still had it.
check(d.get('text_only') is True, 'and it declares itself text only',
      d.get('text_only'))

shared = ('<?xml version="1.0" encoding="UTF-8"?><sst xmlns="%s">'
          '<si><t>Item</t></si><si><t>Amount</t></si></sst>' % SNS)
sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="%s"><sheetData>'
         '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
         '<row r="2"><c r="A2"><v>18000</v></c></row>'
         '</sheetData></worksheet>' % SNS)
xid = put('Accounts', 'accounts.xlsx',
          ooxml({'xl/sharedStrings.xml': shared,
                 'xl/worksheets/sheet1.xml': sheet}))
st, d = call('/api/documents/%d/preview' % xid, tok=TOK)
check(d.get('kind') == 'sheet', 'an .xlsx previews as rows', d.get('kind'))
# Without the shared-string table this row comes back as ['0', '1'].
check((d.get('rows') or [[]])[0] == ['Item', 'Amount'],
      'with its shared strings resolved', (d.get('rows') or [[]])[0])

st, listing = call('/api/documents', tok=TOK)
row = next(x for x in listing['items'] if x['id'] == did)
check(row.get('is_text') is True, 'the listing flags an Office file previewable',
      row.get('is_text'))

# A genuine pre-2007 .doc is not a zip at all.
old_doc = put('Old letter', 'old.doc',
              b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
st, d = call('/api/documents/%d/preview' % old_doc, tok=TOK)
check(st == 415, 'a pre-2007 .doc has no preview', st)


print("\n  --- media plays where it sits ---")
mp4 = put('Clip', 'clip.mp4', b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64)
st, listing = call('/api/documents', tok=TOK)
row = next(x for x in listing['items'] if x['id'] == mp4)
check(row.get('is_video') is True, 'an .mp4 is flagged as video',
      row.get('is_video'))
check(not row.get('is_text'), 'and not as text', row.get('is_text'))
st, d = call('/api/documents/%d/preview' % mp4, tok=TOK)
check(st == 415, 'and has no text preview - it streams from /file instead', st)

mp3 = put('Recording', 'note.mp3', b'ID3' + b'\x00' * 64)
st, listing = call('/api/documents', tok=TOK)
row = next(x for x in listing['items'] if x['id'] == mp3)
check(row.get('is_audio') is True, 'an .mp3 is flagged as audio',
      row.get('is_audio'))


print('\n  --- files that are not clean ---')
# cp1252, not UTF-8: a smart quote as a single 0x92 byte.
put('Windows export', 'win.csv', b'Name,Note\nPriya,it\x92s fine\n')
st, listing = call('/api/documents', tok=TOK)
win = next(x for x in listing['items'] if x['title'] == 'Windows export')
st, d = call('/api/documents/%d/preview' % win['id'], tok=TOK)
check(st == 200, 'a file that is not valid UTF-8 still previews', st)
check(d.get('rows') and len(d['rows']) >= 2,
      'and its rows are still readable', d.get('rows'))

# A genuinely empty upload is refused at 400, so an empty DOCUMENT cannot
# exist — the nearest real case is a file with nothing but a newline.
try:
    upload('Empty', 'empty.txt', b'', TOK)
    check(False, 'an empty upload is refused')
except urllib.error.HTTPError as e:
    check(e.code in (400, 422), 'an empty upload is refused', e.code)

blank = put('Blank', 'blank.txt', b'\n')
st, d = call('/api/documents/%d/preview' % blank, tok=TOK)
check(st == 200 and (d.get('text') or '').strip() == '',
      'a file with only a newline previews as blank', (st, repr(d.get('text'))))

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
