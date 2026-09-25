"""The rule that decides which face represents a person.

THE COMPLAINT THIS ANSWERS, in the owner's words: "I want neat and clear
faces, not overlapping faces and turning half face." Picking the biggest face
gave exactly that — on their library four of the first twelve portraits were
turned away or soft, two of them with the nose so far off centre that the
detector could not find a face in its own crop.

Pure arithmetic: no database, no images, no models. The scoring rule is the
part that rots silently the next time somebody adds a term to it, and the
multiplication is the part worth defending — a sum would let a huge blurred
profile outrank a smaller face looking at the camera, which is the whole bug.
"""
import sys

from app import portraits
from app.vision import frontality

FAIL = []


def check(ok, label, extra=""):
    line = "  %-58s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(line.encode(enc, "replace").decode(enc) + "\n")
    if not ok:
        FAIL.append(label)


print("\nWHICH WAY THE HEAD IS TURNED")
# Eyes at x=100 and x=200. A nose at 150 is square on; at 200 it is over an eye.
square = [(100.0, 100.0), (200.0, 100.0), (150.0, 130.0)]
turned = [(100.0, 100.0), (200.0, 100.0), (190.0, 130.0)]
profile = [(100.0, 100.0), (200.0, 100.0), (205.0, 130.0)]
check(frontality(square) > 0.95, "a nose midway between the eyes is frontal",
      "%.2f" % frontality(square))
check(frontality(turned) < 0.3, "a nose against one eye is turned away",
      "%.2f" % frontality(turned))
check(frontality(profile) == 0.0, "a nose past the eye is a profile",
      "%.2f" % frontality(profile))
check(frontality(None) == 0.0, "no landmarks ranks last, not first")
check(frontality([(1.0, 1.0)]) == 0.0, "too few landmarks ranks last")

# A head tilted right over is a poor portrait even looking straight ahead.
tilted = [(100.0, 100.0), (200.0, 190.0), (150.0, 130.0)]
check(frontality(tilted) < frontality(square),
      "a steeply tilted head scores below an upright one",
      "%.2f vs %.2f" % (frontality(tilted), frontality(square)))

print("\nIS ANYBODY ELSE IN THE CROP")
me = (100.0, 100.0, 100.0, 100.0)
far = [(900.0, 900.0, 100.0, 100.0)]
touching = [(190.0, 100.0, 100.0, 100.0)]
check(portraits.solitude(me, [], 0.6) == 1.0, "alone in the photo is perfect")
check(portraits.solitude(me, far, 0.6) == 1.0,
      "somebody across the photo does not count")
check(portraits.solitude(me, touching, 0.6) < 1.0,
      "somebody beside me lands in the crop",
      "%.2f" % portraits.solitude(me, touching, 0.6))
check(portraits.solitude(me, touching, 0.6) >= 0.25,
      "but never zero - a person only ever in crowds still needs a face")

print("\nSCORING")
# The bug, stated as a test: a big blurred profile must lose to a smaller
# sharp face looking at the camera.
big_blurred_profile = portraits.rank(462, 648, 0.00, 1.0, 0.9)
small_sharp_frontal = portraits.rank(251, 1482, 0.95, 1.0, 0.9)
check(small_sharp_frontal > big_blurred_profile,
      "a sharp frontal face beats a bigger turned one",
      "%.3f vs %.3f" % (small_sharp_frontal, big_blurred_profile))

# Size still matters when everything else is equal - pixels cannot be invented.
check(portraits.rank(300, 800, 0.9, 1.0, 0.9)
      > portraits.rank(120, 800, 0.9, 1.0, 0.9),
      "with all else equal the bigger face wins")
check(portraits.rank(300, 900, 0.9, 1.0, 0.9)
      > portraits.rank(300, 150, 0.9, 1.0, 0.9),
      "with all else equal the sharper face wins")
check(portraits.rank(300, 800, 0.95, 1.0, 0.9)
      > portraits.rank(300, 800, 0.95, 0.4, 0.9),
      "with all else equal the one on their own wins")

# Past a few hundred pixels more size stops helping, so it cannot drown the
# other terms.
check(portraits.size_score(240) == portraits.size_score(4000),
      "size stops counting once there is enough of it")
check(portraits.rank(0, 800, 0.9, 1.0, 0.9) == 0.0,
      "a face with no size scores nothing")

print("\nTWO PEOPLE IN ONE CIRCLE")
alone_ = portraits.rank(300, 800, 0.9, 1.0, 0.9, 1)
crowded = portraits.rank(300, 800, 0.9, 1.0, 0.9, 2)
check(crowded < alone_, "a crop with two faces in it ranks below one with one",
      "%.3f vs %.3f" % (crowded, alone_))
check(portraits.rank(300, 800, 0.9, 1.0, 0.9, 5)
      < portraits.rank(300, 800, 0.9, 1.0, 0.9, 2),
      "and a crowd ranks below a pair")
check(portraits.rank(300, 800, 0.9, 1.0, 0.9, 9) > 0,
      "but a crowd is never zero - somebody only ever in groups needs a face")
# Nothing found at all is not a crowd, and must not be penalised as one: a
# face the detector cannot re-find is already punished through frontality.
check(portraits.rank(300, 800, 0.9, 1.0, 0.9, 0)
      == portraits.rank(300, 800, 0.9, 1.0, 0.9, 1),
      "finding no face is not treated as a crowd")


print("\nIS THIS A FACE WORTH SHOWING AT ALL")
# Every one of these is a real measurement from the owner's library, taken
# from portraits that were rendered and judged by eye. The rule agreed with
# the eye on all twenty-three; these are the six it has to keep rejecting and
# the four nearest misses it has to keep accepting.
rejects = [
    ("a temple carving",      0.82, 0.98, 3706),
    ("a hand across a face",  0.91, 0.47, 2842),
    ("a full profile",        0.91, 0.00, 1183),
    ("a blurred crowd face",  0.90, 0.80,  175),
    ("a face at the score bar but blurred", 0.919, 0.69, 16),
    ("another blurred one",   0.84, 0.71,  159),
    ("badly motion-blurred",  0.92, 0.69,   16),
]
for label, sc, fr, sh in rejects:
    check(not portraits.is_proper(sc, fr, sh), "rejects %s" % label,
          "score %.2f front %.2f sharp %.0f" % (sc, fr, sh))

keeps = [
    ("a face at a slight angle", 0.92, 0.54, 470),
    # Both of these sat just UNDER a 0.92 bar and were being hidden -
    # one of them a person in seven photographs. The bar moved for them.
    ("a child looking up",       0.917, 0.96, 1395),
    ("an elderly man",           0.916, 0.54, 470),
    ("the softest good face",    0.94, 0.92, 314),
    ("a face in sunglasses",     0.94, 0.73, 853),
]
for label, sc, fr, sh in keeps:
    check(portraits.is_proper(sc, fr, sh), "keeps %s" % label,
          "score %.2f front %.2f sharp %.0f" % (sc, fr, sh))

# Each term has to be able to reject on its own, or one of them is decoration.
good = (0.95, 0.90, 800)
check(not portraits.is_proper(0.5, good[1], good[2]),
      "a low detector score alone is enough to reject")
check(not portraits.is_proper(good[0], 0.1, good[2]),
      "being turned away alone is enough")
check(not portraits.is_proper(good[0], good[1], 10),
      "being blurred alone is enough")
check(portraits.is_proper(*good), "and all three together pass")
check(not portraits.is_proper(None, 0.9, 800),
      "a face with no recorded score is not assumed good")


print("\nREADING A RECTANGLE")
check(portraits.parse_bbox("1,2,3,4") == (1.0, 2.0, 3.0, 4.0), "plain values")
check(portraits.parse_bbox(None) is None, "none")
check(portraits.parse_bbox("document") is None, "the document marker")
check(portraits.parse_bbox("1,2,0,5") is None, "a zero-width rectangle")

print("\n%d failing" % len(FAIL))
for f in FAIL:
    print("  - %s" % f)
raise SystemExit(1 if FAIL else 0)
