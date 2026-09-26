"""Can a video that lost a connection ever finish?

Two videos would not upload, and the refusal log showed nothing about any
video at all — which is not "nothing went wrong", it is the instrument being
blind. A chunk is streamed straight to disk, so when a transfer dies
mid-request some of it has already landed and no reply is ever sent. The
phone only counts bytes it was TOLD arrived, so it retries from its last
confirmed offset — and the file on disk is permanently a few hundred
kilobytes ahead of that. Every attempt for the rest of that video's life met
a 409. One dropped packet, and that video could never be backed up again.

This proves the rewind, and proves it does not open the door to the thing the
offset check exists to stop: a chunk spliced in at the wrong place, giving a
file of exactly the right size that is silently corrupt.

Run:  backend/venv/Scripts/python.exe backend/verify_resume_heal.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException  # noqa: E402

ok = True


def check(name, passed, detail=""):
    global ok
    ok = ok and passed
    print("%s  %s%s" % ("PASS" if passed else "FAIL", name,
                        ("  — " + detail) if detail else ""))


# The endpoint's offset logic, lifted out so it can be exercised without a
# server, a database or an account. Kept deliberately identical to the shape
# in gallery.py; the test below asserts the file contents, which is the part
# that actually matters.
def accept(path: str, offset: int, chunk: bytes) -> int:
    have = os.path.getsize(path) if os.path.exists(path) else 0
    if offset < have:
        with open(path, "r+b") as f:
            f.truncate(offset)
        have = offset
    elif offset > have:
        raise HTTPException(409, "Expected offset %d, got %d" % (have, offset))
    with open(path, "ab") as f:
        f.write(chunk)
    return os.path.getsize(path)


tmp = tempfile.mkdtemp()
try:
    # A "video": four 4 MB chunks, each filled with a distinct byte so any
    # splice or duplication shows up as wrong CONTENT, not merely wrong size.
    CH = 4 * 1024 * 1024
    chunks = [bytes([i + 1]) * CH for i in range(4)]
    whole = b"".join(chunks)

    print("== 1. the ordinary case still works ==")
    p = os.path.join(tmp, "a.part")
    at = 0
    for c in chunks:
        at = accept(p, at, c)
    check("four chunks in order", open(p, "rb").read() == whole,
          "%d bytes" % at)

    print()
    print("== 2. a connection that died mid-chunk now recovers ==")
    # Chunks 1 and 2 land. Chunk 3 starts, 900 KB of it reaches the disk, and
    # the connection dies before a reply. The phone still believes 8 MB.
    p = os.path.join(tmp, "b.part")
    accept(p, 0, chunks[0])
    accept(p, CH, chunks[1])
    with open(p, "ab") as f:          # the half-written chunk
        f.write(chunks[2][:900 * 1024])
    stranded = os.path.getsize(p)
    check("the file is ahead of what the phone counted",
          stranded > 2 * CH, "%d on disk vs %d sent" % (stranded, 2 * CH))

    # The phone retries chunk 3 from 8 MB. Before the fix this was 409, for
    # ever.
    at = accept(p, 2 * CH, chunks[2])
    at = accept(p, at, chunks[3])
    got = open(p, "rb").read()
    check("the retry is accepted", at == len(whole), "%d bytes" % at)
    check("and the file is byte-for-byte correct", got == whole)
    check("  ...not merely the right length",
          got[2 * CH:3 * CH] == chunks[2], "chunk 3 intact")

    print()
    print("== 3. a GAP is still refused ==")
    # The other direction is not recoverable and must not be accepted: bytes
    # are missing, so writing this chunk would produce a file of the right
    # size with a hole in it.
    p = os.path.join(tmp, "c.part")
    accept(p, 0, chunks[0])
    try:
        accept(p, 3 * CH, chunks[3])
        check("refuses to write past a hole", False, "it accepted one")
    except HTTPException as e:
        check("refuses to write past a hole", e.status_code == 409, e.detail)

    print()
    print("== 4. a full restart from zero is a rewind, not a duplicate ==")
    # The phone that gives up and starts the video again must not append to
    # what is already there — that was the "right size, silently corrupt"
    # outcome, just at a different offset.
    p = os.path.join(tmp, "d.part")
    accept(p, 0, chunks[0])
    accept(p, CH, chunks[1])
    at = 0
    for c in chunks:
        at = accept(p, at, c)
    check("starting again gives exactly one copy",
          open(p, "rb").read() == whole, "%d bytes" % at)

    print()
    print("== 5. re-sending a chunk already accepted is idempotent ==")
    # A reply lost on the way back: the server has the chunk, the phone never
    # heard so. Sending it again must leave the file unchanged.
    p = os.path.join(tmp, "e.part")
    accept(p, 0, chunks[0])
    accept(p, CH, chunks[1])
    before = open(p, "rb").read()
    accept(p, CH, chunks[1])          # the same chunk, again
    check("the file is unchanged", open(p, "rb").read() == before,
          "%d bytes" % os.path.getsize(p))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
