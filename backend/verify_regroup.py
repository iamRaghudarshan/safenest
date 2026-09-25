"""Face grouping: the rule, and putting a badly grouped library right.

REPORTED FROM THE INSTALLED APP: "faces are grouped wrongly". They were, and
the reason is in how a new face was matched — against every stored face
individually, taking the single best score.

That is fragile in BOTH directions, and this file builds the two failures on
purpose rather than describing them:

  * ONE bad embedding under somebody's name — a blur, a profile — only has to
    beat the threshold once to pull a stranger in after it. Two people become
    one.
  * and a face that is a middling match to all of a person's photos, beating
    none of them outright, starts a new person. One person becomes nine.

Two readings are needed, because the two failures want opposite medicine. A
top-k MEAN is stricter than a nearest-face rule and can only ever be stricter
— a mean never exceeds the maximum it is taken over — so it fixes merges and
cannot fix splits. A CENTROID can be looser, because several ordinary faces
spread around a new one have a centre much closer to it than any of them is
alone. The larger of the two readings is used.

These tests check the arithmetic directly, with vectors built so the OLD rule
demonstrably gets each case wrong — a test that only passes the new rule
proves nothing about what was fixed.

Pure: no server, no database. Vectors are made by hand.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.indexer import FACE_MARGIN, FACE_MATCH, FACE_TOP_K, _best_person  # noqa: E402

FAIL = []


def check(ok, label, extra=''):
    line = '  %-60s %s %s' % (label, 'PASS' if ok else 'FAIL', extra)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    print(line.encode(enc, 'replace').decode(enc, 'replace'))
    if not ok:
        FAIL.append(label)


def unit(vals):
    """A normalised vector, so cosine is a plain dot product."""
    n = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / n for v in vals]


DIM = 16


def basis(i):
    """One direction. Two different basis vectors are ORTHOGONAL — cosine 0 —
    which is what unrelated faces actually look like in a real 512-dimension
    embedding space, and what 2-D vectors cannot represent: in two dimensions
    there is no room for three mutually unrelated people."""
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def blend(a, b, t):
    """t of the way from a to b, normalised. cos(a, blend) falls smoothly from
    1 to 0 as t goes 0 to 1, so every score below is chosen rather than
    discovered."""
    return unit([a[i] * (1 - t) + b[i] * t for i in range(DIM)])


def old_rule(vec, known):
    """What the code used to do: nearest single face wins."""
    best_id, best = None, 0.0
    for pid, other in known:
        s = sum(x * y for x, y in zip(vec, other))
        if s > best:
            best, best_id = s, pid
    return (best_id if best >= FACE_MATCH else None), best


print('  FACE_MATCH=%.2f  TOP_K=%d  MARGIN=%.2f'
      % (FACE_MATCH, FACE_TOP_K, FACE_MARGIN))

A, B, C = basis(0), basis(1), basis(2)


print('\n  --- the outlier that merged two people ---')
# Alice's real faces cluster tightly around A. ONE bad row under her name is
# actually a picture of somebody else, near B — a blur, or a face filed wrong.
# A stranger near B is nothing like Alice, but is almost identical to that one
# bad row.
alice = [(1, blend(A, C, 0.05)), (1, blend(A, C, 0.10)), (1, A),
         (1, blend(B, C, 0.03))]
stranger = B

old_id, old_score = old_rule(stranger, alice)
check(old_id == 1, 'the OLD rule pulls a stranger into Alice',
      'best single match %.3f' % old_score)

new_id, new_score, _runner = _best_person(stranger, alice)
check(not (new_id == 1 and new_score >= FACE_MATCH),
      'the new rule does NOT — her other faces outvote the bad one',
      'score %.3f, under %.2f' % (new_score, FACE_MATCH))


print('\n  --- the variation that split one person ---')
# Bob has four stored faces. Each one, on its own, is a MIDDLING match to a
# new photo of him — 0.35, below the 0.40 bar — so the old rule looked at each
# in turn, found nothing good enough, and started a new person.
#
# But they are spread out among THEMSELVES, and their centre sits much closer
# to the new face than any single one of them does. That is the whole reason
# a centroid can be looser than a nearest-face rule, and it is the realistic
# shape of a wrong split: not two wildly different angles, but several
# ordinary photographs none of which is a standout match.
#
# Built exactly: each stored face is 0.35 of the new one plus a direction
# orthogonal to it and to the others, so cos(new, stored) is 0.35 by
# construction rather than by luck.
SIM = 0.35
rest = math.sqrt(1 - SIM * SIM)
head_on = basis(0)
bob = []
for k in range(4):
    off = basis(3 + k)
    bob.append((2, unit([head_on[i] * SIM + off[i] * rest for i in range(DIM)])))

old_id, old_score = old_rule(head_on, bob)
check(old_id is None,
      'the OLD rule starts a new person for the same man',
      'best single match %.3f, under %.2f' % (old_score, FACE_MATCH))

new_id, new_score, _runner = _best_person(head_on, bob)
check(new_id == 2 and new_score >= FACE_MATCH,
      'the CENTROID keeps him as one person',
      'centre scores %.3f where no single face reached %.2f'
      % (new_score, FACE_MATCH))


print('\n  --- when it genuinely cannot tell ---')
# Two people equidistant from this face. Starting a new group is the
# recoverable mistake: merging two people is tedious to undo by hand, and a
# spare group is one tap to merge.
tie = [(3, B), (3, blend(B, A, 0.05)), (3, blend(B, A, 0.02)),
       (4, C), (4, blend(C, A, 0.05)), (4, blend(C, A, 0.02))]
middle = unit([B[i] + C[i] for i in range(DIM)])
best_id, best_score, runner = _best_person(middle, tie)
check(best_score - runner < FACE_MARGIN,
      'a face between two people is too close to call',
      'best %.3f vs runner-up %.3f' % (best_score, runner))


print('\n  --- the ordinary cases still work ---')
known = [(5, A), (5, blend(A, C, 0.04)), (5, blend(A, C, 0.08)),
         (6, B), (6, blend(B, C, 0.05))]

bid, bscore, run = _best_person(blend(A, C, 0.02), known)
check(bid == 5 and bscore >= FACE_MATCH and bscore - run >= FACE_MARGIN,
      'an obvious match is matched', '%.3f' % bscore)

# Orthogonal to everybody — the shape of a genuinely new face.
bid, bscore, run = _best_person(basis(7), known)
check(not (bscore >= FACE_MATCH and bscore - run >= FACE_MARGIN),
      'a face resembling nobody is not matched', '%.3f' % bscore)

bid, bscore, run = _best_person(A, [])
check(bid is None and bscore == 0.0,
      'the very first face in a library matches nobody')

# A person with ONE stored face must still be matchable: a top-k over a list
# of one is that one, and a centroid of one is itself.
bid, bscore, run = _best_person(blend(A, C, 0.03), [(7, A)])
check(bid == 7 and bscore >= FACE_MATCH,
      'a person with a single face still matches', '%.3f' % bscore)

print()
if FAIL:
    print('  %d FAILED:' % len(FAIL))
    for f in FAIL:
        print('    -', f)
    raise SystemExit(1)
print('  ALL PASS')
