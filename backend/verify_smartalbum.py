"""Smart albums and burst grouping, checked against their real behaviour.

Two things are being proved here and they fail in different ways.

A SMART ALBUM is a stored rule. The failure mode worth catching is not "it
returns the wrong photos" — it is "a rule from a future version, or from a
corrupted row, takes the album down", because that turns one bad string into a
gallery that will not open. So the parser is fed junk on purpose.

A BURST is a grouping decision, and its failure mode is the opposite: it works
on the test data and then swallows a wedding, because the rule was really only
"taken close together". Both halves of the test — time AND likeness — get a
case that would pass if the other half were missing.

Pure: no server, no database, no disk.
"""
from datetime import datetime, timedelta

from app import bursts, smartalbum

FAIL = []


class _Q:
    """The smallest thing apply() can narrow, so the SQL can be read back.

    A real Session would make this an integration test; all that is needed is
    something that records the filters, and SQLAlchemy's own query object
    against the mapped class does exactly that without touching a database.
    """
    def __new__(cls):
        from sqlalchemy.orm import Query
        from app.models import GalleryPhoto
        return Query([GalleryPhoto])


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        FAIL.append(name)


# ---------------------------------------------------------------- rules
print("\nSmart album rules")

check("a plain rule survives the round trip",
      smartalbum.parse(smartalbum.dumps({"label": "beach", "year": 2024})),
      {"label": "beach", "year": 2024})

# The whole reason the rule is a dict of known keys rather than stored SQL.
check("an unknown key is dropped, not obeyed",
      smartalbum.parse('{"label":"beach","drop_table":"users"}'),
      {"label": "beach"})

# A rule row that a newer version wrote, or that got truncated, must not be
# able to stop the album opening.
for junk in ("", None, "not json at all", "[1,2,3]", '"a string"', "{", "null"):
    got = smartalbum.parse(junk)
    if got != {}:
        FAIL.append(f"junk rule {junk!r}")
        print(f"  FAIL  junk {junk!r} parsed to {got!r}")
print(f"  PASS  seven shapes of junk all parse to an empty rule")

check("empty values are not kept as filters",
      smartalbum.parse('{"label":"","year":null,"kind":"video"}'),
      {"kind": "video"})

# The subtitle is what tells somebody why these photos are here.
check("the rule reads back in words",
      smartalbum.describe({"label": "beach", "year": 2024, "kind": "video"}),
      "beach, videos, 2024")
check("an empty rule still describes itself",
      smartalbum.describe({}), "everything")
check("a month becomes a month name",
      smartalbum.describe({"month": 3}), "March")
check("a nonsense month is skipped rather than crashing",
      smartalbum.describe({"month": 99, "year": 2024}), "2024")
# Found by a browser test, not by this file: a photos-only rule described
# itself as "everything", because only "video" was ever named. An album whose
# rule is invisible is the thing describe() exists to prevent.
check("a photos-only rule says so", smartalbum.describe({"kind": "photo"}), "photos")
# The matching half of the same bug, found the same way: `kind` is NULL for
# photos and only written for videos, so a rule of {"kind": "photo"} compiled
# to `kind == 'photo'` matched nothing. apply() now uses the gallery's own
# test. Asserted on the SQL rather than on rows, so this stays a pure test.
# NOTE the signature: check(name, got, want) here, compared by equality —
# not the check(ok, label, extra) the browser harnesses use. Mixing them up
# reports a passing assertion as a failure, which is how this very line first
# went red.
_sql = str(smartalbum.apply(_Q(), None, 1, {"kind": "photo"})).upper()
# Only the two positive properties. A third probe for the absence of
# "= :KIND" was always true, because "!= :KIND_1" contains it — a substring
# test for an operator cannot tell = from !=.
check("a photos rule matches NULL and excludes video",
      ("KIND IS NULL" in _sql, "KIND !=" in _sql), (True, True))
check("a videos-only rule still says so", smartalbum.describe({"kind": "video"}), "videos")


# ---------------------------------------------------------------- bursts
print("\nBurst grouping")


class P:
    def __init__(self, i, t, h):
        self.id, self.shot_at, self.phash = i, t, h
    def __repr__(self):
        return f"P{self.id}"


T = datetime(2024, 5, 1, 12, 0, 0)
SAME = "ffffffffffffff00"       # eight frames of one shot
NEAR = "ffffffffffffff01"       # one bit different: still the same shot
OTHER = "0000000000000000"      # nothing like it


def ids(groups):
    return [[p.id for p in g] for g in groups]


# Five frames, two seconds apart, all alike: one burst.
run = [P(i, T + timedelta(seconds=2 * i), SAME if i % 2 else NEAR) for i in range(5)]
check("five near-identical frames two seconds apart make one burst",
      ids(bursts.group(run)), [[0, 1, 2, 3, 4]])

# THE WEDDING. Same timing exactly, but every frame is different. If the rule
# were only "close in time" this would collapse a hundred distinct photos into
# one tile and hide the event.
# Written out rather than generated: the first attempt at this test used
# f"{i:016x}", whose consecutive values differ by one or two BITS — so those
# "different" photos were near-identical and the grouper was right to join
# them. Test data for a likeness rule has to have its likeness checked too,
# which is what the assert below is for.
DISTINCT = ["0000000000000000", "ffffffff00000000", "0f0f0f0f0f0f0f0f",
            "aaaaaaaaaaaaaaaa", "00ff00ff00ff00ff"]
assert all(bursts.distance(a, b) > bursts.MAX_DISTANCE
           for a, b in zip(DISTINCT, DISTINCT[1:])), "test data is not distinct"
wedding = [P(i, T + timedelta(seconds=2 * i), DISTINCT[i]) for i in range(5)]
check("photos taken seconds apart but all different are NOT a burst",
      ids(bursts.group(wedding)), [[0], [1], [2], [3], [4]])

# THE SAME VIEW, MONTHS APART. Identical hash, no shared moment.
seasons = [P(0, T, SAME), P(1, T + timedelta(days=90), SAME),
           P(2, T + timedelta(days=180), SAME)]
check("the same view photographed months apart is NOT a burst",
      ids(bursts.group(seasons)), [[0], [1], [2]])

# A pair is not a burst: badging one of two helps nobody.
pair = [P(0, T, SAME), P(1, T + timedelta(seconds=1), NEAR)]
check("two alike frames stay two photos",
      ids(bursts.group(pair)), [[0], [1]])

# A burst, then unrelated photos, then another burst.
mixed = ([P(i, T + timedelta(seconds=i), SAME) for i in range(3)]
         + [P(10, T + timedelta(minutes=5), OTHER)]
         + [P(20 + i, T + timedelta(minutes=10, seconds=i), NEAR) for i in range(3)])
check("two bursts either side of a loose photo",
      ids(bursts.group(mixed)), [[0, 1, 2], [10], [20, 21, 22]])

# Missing data must not group anything: a photo with no hash has not been
# shown to be like anything.
nohash = [P(0, T, None), P(1, T + timedelta(seconds=1), None),
          P(2, T + timedelta(seconds=2), None)]
check("photos with no perceptual hash are never grouped",
      ids(bursts.group(nohash)), [[0], [1], [2]])

# Neither must a bad one.
bad = [P(i, T + timedelta(seconds=i), "zzzz") for i in range(3)]
check("an unparseable hash is not treated as a match",
      ids(bursts.group(bad)), [[0], [1], [2]])

check("an empty library groups into nothing", bursts.group([]), [])


print()
if FAIL:
    print(f"FAILED: {len(FAIL)}")
    for f in FAIL:
        print(f"  - {f}")
    raise SystemExit(1)
print("All smart-album and burst checks passed.")
