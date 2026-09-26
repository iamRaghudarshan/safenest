"""Does the upload still refuse photographs it could have kept?

Three uploads were refused on the owner's phone with "Unsupported image", and
for weeks that was all anybody knew. The live refusal log (added because
asking somebody to read an error off their phone three times is not a
diagnostic method) finally named them, and they were three different faults:

    .dng, starts 49492a00...  cannot identify image file
    .jpg, starts ffd8ffe0...  Truncated File Read
    ?,    starts ffd8ffe0...  image file is truncated (0 bytes not processed)

Two of those are photographs this server could perfectly well have stored.
This proves it now does, and — just as important — that a file which really
is unreadable is still refused rather than quietly turned into something.

Run:  backend/venv/Scripts/python.exe backend/verify_decode_recovery.py
"""
import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

from app.routers.gallery import _decodable, _embedded_jpeg  # noqa: E402

ok = True


def check(name: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print("%s  %s%s" % ("PASS" if passed else "FAIL", name,
                        ("  — " + detail) if detail else ""))


def jpeg(w: int, h: int, colour=(120, 30, 40)) -> bytes:
    """A real JPEG of a given size, not a stub — the point is to decode it."""
    im = Image.new("RGB", (w, h), colour)
    # Noise, so the encoder cannot collapse it to a few hundred bytes and make
    # the "largest preview wins" test meaningless.
    px = im.load()
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            px[x, y] = ((x * 7) % 256, (y * 13) % 256, (x * y) % 256)
    b = io.BytesIO()
    im.save(b, format="JPEG", quality=88)
    return b.getvalue()


def decodes(data: bytes) -> tuple[bool, str]:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return True, "%dx%d %s" % (im.width, im.height, im.format)
    except Exception as exc:                       # noqa: BLE001
        return False, type(exc).__name__ + ": " + str(exc)[:60]


print("== 1. an ordinary photo is untouched ==")
# The fast path has to stay the fast path: nearly every upload is this, and a
# recovery pass that re-encodes healthy photographs would quietly lose quality
# on the whole library.
clean = jpeg(600, 400)
out = _decodable(clean, "IMG_1.jpg")
check("returned byte-for-byte", out is clean or out == clean,
      "%d bytes in, %d out" % (len(clean), len(out)))


print()
print("== 2. a truncated JPEG is kept, not refused ==")
# This is the real shape: the file ends before the image data does. Pillow
# refuses it by default, which is how it reached the log.
cut = clean[:int(len(clean) * 0.72)]
before, why = decodes(cut)
check("Pillow refuses it on its own", not before, why)
fixed = _decodable(cut, "IMG_2.jpg")
after, what = decodes(fixed)
check("recovered into something that opens", after, what)
check("and it is a real picture, not a blank", len(fixed) > 4096,
      "%d bytes" % len(fixed))

# EXIF survives, because the whole app organises by date taken.
with_exif = Image.new("RGB", (300, 200), (10, 90, 160))
b = io.BytesIO()
exif = with_exif.getexif()
exif[306] = "2019:07:14 11:02:33"          # DateTime
with_exif.save(b, format="JPEG", exif=exif.tobytes(), quality=90)
whole = b.getvalue()
trunc = whole[:int(len(whole) * 0.7)]
rec = _decodable(trunc, "IMG_3.jpg")
got = Image.open(io.BytesIO(rec)).getexif().get(306)
check("the date taken survives the repair", got == "2019:07:14 11:02:33",
      "read back %r" % (got,))


print()
print("== 3. a RAW file gives up its preview ==")
# A DNG is a TIFF container with finished JPEGs inside it. Built here the way
# a camera builds one: a TIFF header, a small thumbnail, then the full-size
# preview. Pillow cannot identify the container; the preview is an ordinary
# JPEG and always could have been stored.
thumb = jpeg(160, 120)
preview = jpeg(1600, 1200)
dng = (bytes.fromhex("49492a00") + struct.pack("<I", 8) + b"\x00" * 64
       + thumb + b"JUNKJUNK" + preview + b"\x00" * 32)

before, why = decodes(dng)
check("Pillow refuses the container", not before, why)

inner = _embedded_jpeg(dng)
check("found a JPEG inside", inner is not None)
# LARGEST, not first. The first one found is the thumbnail, and storing that
# would look like the upload worked while replacing the photograph with a
# postage stamp.
im = Image.open(io.BytesIO(inner))
check("took the full-size preview, not the thumbnail",
      (im.width, im.height) == (1600, 1200), "%dx%d" % (im.width, im.height))

out = _decodable(dng, "IMG_4.dng")
after, what = decodes(out)
check("so the upload path can decode it", after, what)

# Named only by extension, with no TIFF magic — some cameras and some
# exporters write one without the other.
out2 = _decodable(b"\x89SOMETHINGELSE" + preview, "IMG_5.nef")
after2, what2 = decodes(out2)
check("and recognises a RAW by extension too", after2, what2)


print()
print("== 4. genuinely unreadable is still refused ==")
# The failure mode to design against is a recovery pass so eager that every
# upload "succeeds" and the library fills with rubbish. Nothing here contains
# a JPEG, so nothing should come back decodable.
for name, junk in [
    ("random bytes", bytes(range(256)) * 40),
    ("a text file", b"this is not a photograph, it is a sentence\n" * 200),
    ("an empty-ish blob", b"\x01\x02\x03" * 10),
]:
    out = _decodable(junk, "x.bin")
    after, _ = decodes(out)
    check("%s is not turned into an image" % name, not after)
    check("  ...and the original is handed back so the real error is raised",
          out == junk)


print()
print("ALL PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
