"""Every call the phone now makes, sent with the phone's own body shape.

WHY THIS AND NOT A UNIT TEST. `flutter analyze` proves the Dart compiles and
the widget tests prove it lays out; neither of them ever speaks to the server,
so a body the server rejects — a missing field, a `{}` where a dict is
required, a route that takes no body at all — compiles, lays out, passes, and
then fails on somebody's phone. The endpoint diff that drove this work has the
same blind spot: it proves the phone MENTIONS the path.

So each case below is written from the Dart, not from the router. Where the
phone sends `const {}` this sends `{}`; where it sends `{'person_id': n}` this
sends that. A 422 here is a real bug even though both sides look right on
their own.
"""
import json
import sys
import urllib.error
import urllib.request

B = 'http://127.0.0.1:8099'
FAIL = []


def check(ok, label, extra=''):
    line = '  %-56s %s %s' % (label, 'PASS' if ok else 'FAIL', extra)
    # cp1252 console: an em dash in a server message kills the run otherwise.
    sys.stdout.write(line.encode(sys.stdout.encoding or 'utf-8',
                                 'replace').decode(sys.stdout.encoding
                                                   or 'utf-8') + '\n')
    if not ok:
        FAIL.append(label)


def call(path, body=None, method=None, tok=None, files=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data,
        method=method or ('POST' if data is not None else 'GET'),
        headers={'Content-Type': 'application/json',
                 **({'Authorization': 'Bearer ' + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode('utf-8', 'replace')
            try:
                return r.status, json.loads(raw or '{}')
            except ValueError:
                return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            return e.code, {}


st, d = call('/api/auth/login', {'email': 'priya@example.com',
                                 'password': 'DemoHouse#2026'})
if st != 200:
    print('login failed: %s %s' % (st, d))
    raise SystemExit(1)
TOK = d['token']

print('\nPHOTOS — bulk, archive, tagging')

st, lib = call('/api/gallery?limit=6', tok=TOK)
ids = [p['id'] for p in lib.get('items', [])][:3]
check(bool(ids), 'there are photos to work with', '%d' % len(ids))

if ids:
    # The phone's _bulk(): {'action': ..., 'ids': [...]}.
    st, r = call('/api/gallery/bulk', {'action': 'favourite', 'ids': ids},
                 tok=TOK)
    check(st == 200 and r.get('changed') == len(ids),
          'bulk favourite sets (not toggles)', '%s %s' % (st, r))

    # Sent TWICE on purpose. The per-photo route toggles, so a second call
    # there would un-star them; this is the bug the switch to /bulk fixes and
    # it only shows on the second press.
    st, r = call('/api/gallery/bulk', {'action': 'favourite', 'ids': ids},
                 tok=TOK)
    st2, after = call('/api/gallery?limit=6', tok=TOK)
    still = [p for p in after.get('items', [])
             if p['id'] in ids and (p.get('is_favourite')
                                    or p.get('is_favorite'))]
    check(len(still) == len(ids),
          'starring twice leaves them starred', '%d of %d' % (len(still),
                                                              len(ids)))

    st, r = call('/api/gallery/archive/bulk', {'ids': ids, 'archived': True},
                 tok=TOK)
    check(st == 200 and r.get('changed') == len(ids),
          'bulk archive', '%s %s' % (st, r))
    st, r = call('/api/gallery/archive/bulk', {'ids': ids, 'archived': False},
                 tok=TOK)
    check(st == 200, 'bulk un-archive puts them back', str(st))

    # Tagging by NAME — the shape the picker sends for somebody new.
    st, r = call('/api/gallery/%d/tag' % ids[0], {'name': 'Wire Test Person'},
                 tok=TOK)
    check(st == 200 and r.get('person_id'), 'tag by a new name', '%s %s'
          % (st, r))
    pid = r.get('person_id')

    if pid:
        # And by id — the shape it sends for somebody already known.
        st, r = call('/api/gallery/%d/tag' % ids[1] if len(ids) > 1
                     else '/api/gallery/%d/tag' % ids[0],
                     {'person_id': pid}, tok=TOK)
        check(st == 200, 'tag an existing person by id', str(st))

        st, r = call('/api/gallery/%d/untag' % ids[0], {'person_id': pid},
                     tok=TOK)
        check(st == 200 and r.get('ok'), 'untag', '%s %s' % (st, r))

print('\nPEOPLE — the correction tools')

st, people = call('/api/people', tok=TOK)
plist = people.get('people', [])
check(st == 200, 'people list', str(st))
# The face crop the owner reported missing.
withbox = [p for p in plist if isinstance(p.get('box'), dict)]
check(bool(plist) and bool(withbox),
      'people carry a face box, not just a cover',
      '%d of %d' % (len(withbox), len(plist)))

if plist:
    one = plist[0]['id']
    st, f = call('/api/people/%d/faces' % one, tok=TOK)
    check(st == 200 and 'items' in f, 'faces of one person', str(st))
    faces = f.get('items', [])
    check(all(isinstance(x.get('box'), dict) for x in faces) if faces else True,
          'each face carries its own box', '%d faces' % len(faces))

    # The phone posts `const {}` here, and the route declares no body at all.
    st, r = call('/api/people/%d/me' % one, {}, tok=TOK)
    check(st == 200 and r.get('is_me') == 1,
          'mark as me accepts an empty body', '%s %s' % (st, r))

    if faces:
        fid = faces[0]['face_id']
        st, r = call('/api/people/faces/%d/assign' % fid, {'person_id': None},
                     tok=TOK)
        check(st == 200, 'detach a face (person_id null)', '%s %s' % (st, r))
        st, r = call('/api/people/faces/%d/assign' % fid, {'person_id': one},
                     tok=TOK)
        check(st == 200, 'and put it back', str(st))

    if len(plist) > 1:
        other = plist[1]['id']
        st, f2 = call('/api/people/%d/faces' % other, tok=TOK)
        picks = [x['face_id'] for x in f2.get('items', [])][:1]
        if picks:
            st, r = call('/api/people/%d/split' % other,
                         {'face_ids': picks}, tok=TOK)
            check(st == 200, 'split faces out to a new person',
                  '%s %s' % (st, r))
    # An empty split must be refused rather than silently doing nothing.
    st, r = call('/api/people/%d/split' % one, {'face_ids': []}, tok=TOK)
    check(st == 422, 'an empty split is refused', str(st))

print('\nFILTERING BY SEVERAL FACES')

st, pl = call('/api/people', tok=TOK)
ids = [p['id'] for p in pl.get('people', [])][:2]
if len(ids) < 2:
    check(True, 'SKIPPED: fewer than two people', '')
else:
    def count(person):
        s, d = call('/api/gallery?limit=1&person=%s' % person, tok=TOK)
        return s, d.get('total', -1)

    s1, a = count(ids[0])
    s2, b = count(ids[1])
    s3, both = count('%d,%d' % (ids[0], ids[1]))
    check(s1 == 200 and a >= 0, 'one id still works (the old shape)', str(a))
    check(s3 == 200, 'two ids are accepted', str(s3))
    # The property that matters. OR would return MORE the more faces were
    # picked, which is the opposite of what choosing a second face is for.
    # `both >= 0` matters: on a refusal `total` is absent and the default is
    # -1, which is <= everything and passes a test that has just proved
    # nothing. A check that cannot fail on an error is not a check.
    check(both >= 0 and both <= min(a, b),
          'several faces NARROW the result, never widen it',
          '%d and %d together -> %d' % (a, b, both))
    s4, junk = count('%d,abc,,-9999999' % ids[0])
    check(s4 == 404 or junk == a,
          'junk in the list is ignored or refused, never a 500', str(s4))


print('\nDOCUMENTS — the eight that were missing')

st, docs = call('/api/documents?limit=5', tok=TOK)
items = docs.get('items', [])
check(st == 200, 'documents list', str(st))

st, r = call('/api/documents/kinds', tok=TOK)
kinds = r.get('items', [])
check(st == 200 and bool(kinds), 'the kinds list', '%d kinds' % len(kinds))

st, r = call('/api/documents/recent', tok=TOK)
check(st == 200 and all(k in r for k in ('added', 'changed', 'starred')),
      'recent returns three lists', str(st))

st, r = call('/api/documents/trash', tok=TOK)
check(st == 200 and 'items' in r, 'the recycle bin lists', str(st))

if items:
    did = items[0]['id']

    # `const {}` again — this route DOES declare Body(...), so an empty dict
    # is the case worth proving rather than assuming.
    st, r = call('/api/documents/%d/favourite' % did, {}, tok=TOK)
    check(st == 200 and 'is_favourite' in r,
          'star a document with an empty body', '%s %s' % (st, r))
    call('/api/documents/%d/favourite' % did, {}, tok=TOK)   # put it back

    st, r = call('/api/documents/%d/copy' % did, {}, tok=TOK)
    check(st == 200 and r.get('item'), 'copy with an empty body', str(st))
    copy_id = (r.get('item') or {}).get('id')

    st, r = call('/api/documents/%d/kind' % did, {'kind': kinds[0]}, tok=TOK)
    check(st == 200 and r.get('kind_source') == 'user',
          'correcting the type records that a HUMAN chose it',
          '%s %s' % (st, r))
    st, r = call('/api/documents/%d/kind' % did, {'kind': None}, tok=TOK)
    check(st == 200 and r.get('kind') is None,
          'and "none of these" clears it', str(st))

    st, r = call('/api/documents/%d/suggestions' % did, tok=TOK)
    check(st == 200 and 'ready' in r, 'scan suggestions', '%s ready=%s'
          % (st, r.get('ready')))

    if copy_id:
        # The whole bin round trip, on a document made for the purpose so
        # nothing real is risked.
        st, _ = call('/api/documents/%d' % copy_id, method='DELETE', tok=TOK)
        check(st == 200, 'delete the copy to the bin', str(st))
        st, r = call('/api/documents/trash', tok=TOK)
        inbin = [x for x in r.get('items', []) if x['id'] == copy_id]
        check(bool(inbin), 'it appears in the bin')
        check(bool(inbin) and inbin[0].get('trashed_fmt'),
              'and carries the date the phone shows')
        st, r = call('/api/documents/%d/restore' % copy_id, {}, tok=TOK)
        check(st == 200, 'restore with an empty body', str(st))
        st, _ = call('/api/documents/%d' % copy_id, method='DELETE', tok=TOK)
        st, r = call('/api/documents/%d/permanent' % copy_id, method='DELETE',
                     tok=TOK)
        check(st == 200, 'delete it for good', '%s %s' % (st, r))

print('\nSUGGESTED ALBUMS')
st, r = call('/api/gallery/albums/suggested', tok=TOK)
check(st == 200 and 'suggestions' in r, 'suggested albums', str(st))
for s in r.get('suggestions', [])[:1]:
    check(all(k in s for k in ('name', 'photo_ids', 'count')),
          'a suggestion carries what the phone posts back',
          ','.join(sorted(s.keys()))[:60])

print('\n%d failing' % len(FAIL))
for f in FAIL:
    print('  - %s' % f)
raise SystemExit(1 if FAIL else 0)
