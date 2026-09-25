"""Which face becomes a person's portrait, and why.

THE BUG THIS PINS. The People page showed coloured blurs nobody could name.
Two separate causes, and fixing only one would have left it broken:

  1. The crop was taken from a 360x480 THUMBNAIL, where a face in a group shot
     is 19-34 pixels. The rectangle was right and there was nothing behind it.
  2. The face came from `cover_id` — whichever photo the person was FIRST seen
     in, which has nothing to do with how well it shows their face. On a real
     library 15 of 36 people had a face elsewhere 1.4x to 3.2x bigger.

Pure arithmetic over face rectangles, so it runs anywhere with no database,
no images and no models. The picking rule is the part that rots silently the
next time somebody adds a condition to it.
"""
import sys

from app.routers.people import (MIN_PORTRAIT_PX, PORTRAIT_SIZE_TIE,
                                best_portrait, face_size)

FAIL = []


def check(ok, label, extra=""):
    line = "  %-56s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(line.encode(enc, "replace").decode(enc) + "\n")
    if not ok:
        FAIL.append(label)


class F:
    """Just enough of a PhotoFace for the chooser."""

    def __init__(self, fid, bbox, score=0.9, photo_id=1):
        self.id, self.bbox, self.score, self.photo_id = fid, bbox, score, photo_id

    def __repr__(self):
        return "F(%s)" % self.id


print("\nREADING A RECTANGLE")
check(face_size("10,20,300,400") == 400, "the longer side wins", face_size("10,20,300,400"))
check(face_size("10,20,400,300") == 400, "whichever way round it is")
check(face_size(None) == 0, "a face with no rectangle measures zero")
check(face_size("document") == 0, "and so does the document marker")
check(face_size("1,2,0,0") == 0, "a zero-sized rectangle is not a face")

print("\nPICKING THE PORTRAIT")
small = F(1, "0,0,80,80")
big = F(2, "0,0,300,300", photo_id=7)
check(best_portrait([small, big]) is big,
      "the bigger face wins", best_portrait([small, big]))

# The whole point: the cover photo does not get a say.
cover_face = F(1, "0,0,93,93", photo_id=1)
elsewhere = F(2, "0,0,265,265", photo_id=99)
check(best_portrait([cover_face, elsewhere]) is elsewhere,
      "a better face in ANOTHER photo beats the cover", "photo 99")

# Confidence decides between faces of a similar size, so a big blurred
# half-profile does not beat a slightly smaller one looking at the camera.
blurry = F(1, "0,0,300,300", score=0.55)
sharp = F(2, "0,0,270,270", score=0.99)
check(best_portrait([blurry, sharp]) is sharp,
      "at a similar size, the confident one wins", "0.99 over 0.55")

# ... but only at a SIMILAR size. Confidence must never beat a face that is
# actually much bigger, because pixels cannot be invented.
tiny_sure = F(1, "0,0,70,70", score=1.0)
huge_ok = F(2, "0,0,400,400", score=0.7)
check(best_portrait([tiny_sure, huge_ok]) is huge_ok,
      "confidence does not beat a much bigger face", "400px over 70px")

print("\nWHEN THERE IS NOTHING GOOD")
runt = F(1, "0,0,%d,%d" % (MIN_PORTRAIT_PX - 20, MIN_PORTRAIT_PX - 20))
runt2 = F(2, "0,0,%d,%d" % (MIN_PORTRAIT_PX - 40, MIN_PORTRAIT_PX - 40))
got = best_portrait([runt2, runt])
check(got is runt,
      "all too small: the largest is still shown, not nothing", got)
check(best_portrait([]) is None, "no faces at all returns None")
check(best_portrait([F(1, None), F(2, "document")]) is None,
      "faces with no rectangle return None")

# A face exactly on the boundary must be usable, or the threshold silently
# means "greater than" and one pixel decides whether somebody is nameable.
edge = F(1, "0,0,%d,%d" % (MIN_PORTRAIT_PX, MIN_PORTRAIT_PX))
check(best_portrait([edge]) is edge, "the minimum size itself counts as usable")

print("\nTHE TIE WINDOW")
# Pinning the constant's meaning: just inside the window is a tie decided by
# score, just outside it is not.
biggest = F(1, "0,0,100,100", score=0.5)
inside = F(2, "0,0,%d,%d" % (int(100 * PORTRAIT_SIZE_TIE) + 1,
                             int(100 * PORTRAIT_SIZE_TIE) + 1), score=0.99)
outside = F(3, "0,0,%d,%d" % (int(100 * PORTRAIT_SIZE_TIE) - 5,
                              int(100 * PORTRAIT_SIZE_TIE) - 5), score=0.99)
check(best_portrait([biggest, inside]) is inside,
      "inside the window, score decides")
check(best_portrait([biggest, outside]) is biggest,
      "outside it, size decides")

print("\n%d failing" % len(FAIL))
for f in FAIL:
    print("  - %s" % f)
raise SystemExit(1 if FAIL else 0)
