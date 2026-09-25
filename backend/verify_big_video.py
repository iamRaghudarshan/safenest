"""Can a large video be filed without holding it in memory?

THE COMPLAINT: "some photos and videos not moving showing error... it should
be able to backup any size it may 100gb also."

Two separate ceilings were in the way, and lifting only one would have left it
broken. The chunked upload refused anything over 256 MB, which is a few
minutes of 4K. And when it did accept a file it read the whole thing back off
disk to file it — so a clip that had uploaded perfectly well failed at the
last step, on a server that lives on somebody's laptop.

The re-mux that moves `moov` to the front is the delicate part: it rewrites
the chunk offset tables, and getting it wrong corrupts somebody's only copy of
a video in a way nothing would notice until they tried to play it years later.
So the streaming version is not merely tested for plausibility — it is
compared BYTE FOR BYTE against the in-memory implementation that has been
shipping, on the same input. Identical output, or it is wrong.

No server, no database, no network: this builds real MP4 containers and runs
the functions.
"""
import hashlib
import os
import struct
import sys
import tempfile
import tracemalloc

from app.routers import gallery as g

MB = 1024 * 1024
FAIL = []


def check(ok, label, extra=""):
    line = "  %-56s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(line.encode(enc, "replace").decode(enc) + "\n")
    if not ok:
        FAIL.append(label)


def atom(typ, body):
    return struct.pack(">I", len(body) + 8) + typ + body


def moov_atom(timescale=600, duration=3000):
    mvhd = struct.pack(">IIIII", 0, 0, 0, timescale, duration) + b"\x00" * 80
    return atom(b"moov", atom(b"mvhd", mvhd))


def slow_start(media: bytes) -> bytes:
    """The iPhone shape: ftyp, media, then the index at the very end."""
    return (atom(b"ftyp", b"isom" + b"\x00" * 8)
            + struct.pack(">I", len(media) + 8) + b"mdat" + media
            + moov_atom())


def fast_start(media: bytes) -> bytes:
    return (atom(b"ftyp", b"isom" + b"\x00" * 8) + moov_atom()
            + struct.pack(">I", len(media) + 8) + b"mdat" + media)


tmp = tempfile.mkdtemp()

print("\nTHE RE-MUX IS THE SAME ONE, DONE WITHOUT THE MEMORY")
media = os.urandom(3 * MB)
raw = slow_start(media)
src = os.path.join(tmp, "a.mp4")
dst = os.path.join(tmp, "b.mp4")
open(src, "wb").write(raw)

in_memory = g._faststart(raw)
streamed_ok = g.faststart_file(src, dst)
streamed = open(dst, "rb").read() if streamed_ok else None

check(in_memory is not None, "the shipping in-memory re-mux still works")
check(streamed_ok, "the streaming one rewrites the same file")
check(in_memory == streamed,
      "and produces BYTE-FOR-BYTE the same output",
      hashlib.sha256(streamed or b"").hexdigest()[:16])
check(len(streamed or b"") == len(raw), "the length is unchanged")

print("\nIT REFUSES WHAT IT SHOULD, SO NOTHING IS CORRUPTED")
fastsrc = os.path.join(tmp, "c.mp4")
open(fastsrc, "wb").write(fast_start(media))
check(not g.faststart_file(fastsrc, os.path.join(tmp, "d.mp4")),
      "an already fast-start clip is left alone")
junk = os.path.join(tmp, "e.mp4")
open(junk, "wb").write(os.urandom(5000))
check(not g.faststart_file(junk, os.path.join(tmp, "f.mp4")),
      "something that is not an MP4 is refused, not rewritten")
empty = os.path.join(tmp, "g.mp4")
open(empty, "wb").write(b"")
check(not g.faststart_file(empty, os.path.join(tmp, "h.mp4")),
      "an empty file is refused")

print("\nSIZE STOPS MATTERING")
big = os.path.join(tmp, "big.mp4")
chunk = os.urandom(MB)
with open(big, "wb") as f:
    f.write(atom(b"ftyp", b"isom" + b"\x00" * 8))
    f.write(struct.pack(">I", 300 * MB + 8) + b"mdat")
    for _ in range(300):
        f.write(chunk)
    f.write(moov_atom(timescale=1000, duration=12000))
size = os.path.getsize(big)

tracemalloc.start()
ok = g.faststart_file(big, os.path.join(tmp, "bigfast.mp4"))
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
check(ok, "a 300 MB clip is re-muxed", "%.0f MB" % (size / MB))
# The whole point. The old path needed the file twice over; this needs a
# window, whatever the file weighs.
check(peak < 64 * MB, "and does it in well under 64 MB of memory",
      "peak %.0f MB for a %.0f MB file" % (peak / MB, size / MB))

tracemalloc.start()
digest = g.hash_file(big)
_, peak2 = tracemalloc.get_traced_memory()
tracemalloc.stop()
check(len(digest) == 64, "the digest is streamed too")
check(peak2 < 64 * MB, "also in a fixed window",
      "peak %.0f MB" % (peak2 / MB))

with open(big, "rb") as f:
    check(digest == hashlib.sha256(f.read()).hexdigest(),
          "and matches what hashing it whole would give")

print("\nTHE DURATION STILL READS")
check(g.mov_duration_file(os.path.join(tmp, "bigfast.mp4")) == 12000,
      "from the header alone, not by decoding", "12000 ms")
check(g.mov_duration_file(junk) is None, "junk reports no duration")

print("\nA VIDEO WITH NO FILENAME IS STILL A VIDEO")
# THE BUG THIS PINS, taken from the failure log on the owner's own machine:
#
#   21:26:22  413 /api/gallery/upload/chunk?...&filename=&duration_ms=27000
#             &offset=29360128
#
# 29360128 is 28 MB. The phone sends no filename; the limit was chosen by
# `looks_like_video(b"", filename)`, and with no name the answer was "photo".
# So every video got the 30 MB photo ceiling and died one chunk later. They
# were ordinary 20-40 second clips, and not one of them could ever arrive.


def limit_for(duration_ms, head, filename):
    """The rule as the endpoint now applies it."""
    is_video = bool(duration_ms) or g.looks_like_video(head, filename)
    return g.MAX_CHUNKED_BYTES if is_video else g.MAX_BYTES


check(limit_for(27000, b"", "") == g.MAX_CHUNKED_BYTES,
      "a duration alone makes it a video", "no filename, no bytes yet")
check(limit_for(27000, b"", "") > 29360128 + 4 * MB,
      "so the chunk that used to 413 at 28 MB now fits")

mp4_head = struct.pack(">I", 24) + b"ftyp" + b"isom" + b"\x00" * 12
check(limit_for(0, mp4_head, "") == g.MAX_CHUNKED_BYTES,
      "the magic bytes alone make it a video", "no duration, no filename")
check(limit_for(0, b"", "clip.mp4") == g.MAX_CHUNKED_BYTES,
      "and the filename still works when there is one")

check(limit_for(0, b"\xff\xd8\xff\xe0" + b"\x00" * 20, "") == g.MAX_BYTES,
      "a JPEG is still held to the photo limit")
check(limit_for(0, b"", "") == g.MAX_BYTES,
      "and something with no evidence at all stays cautious")


print("\nTHE CEILING")
check(g.MAX_CHUNKED_BYTES > 4 * 1024 * MB,
      "a chunked upload allows multi-gigabyte clips",
      "%.0f GB" % (g.MAX_CHUNKED_BYTES / (1024 * MB)))
check(g.MAX_CHUNKED_BYTES > g.MAX_VIDEO_BYTES,
      "and more than a single-request upload, which holds it in memory")

print("\n%d failing" % len(FAIL))
for f in FAIL:
    print("  - %s" % f)
raise SystemExit(1 if FAIL else 0)
