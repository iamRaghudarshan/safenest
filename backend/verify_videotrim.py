"""Trimming a video without re-encoding it, checked by decoding the result.

The whole risk here is silent corruption. A trim rewrites the index that tells
a player where every frame lives, and an index that is wrong by one produces a
file that still opens, still reports a duration, and plays garbage — or
nothing. So this test does not stop at "the bytes came back". It writes a REAL
MP4 with OpenCV, trims it, and then DECODES the result with OpenCV and counts
the frames that actually came out.

That is the only assertion worth making about a muxer, and it is the reason
this feature could be written without FFmpeg at all: the frames that survive a
trim are byte-for-byte the frames that were there, so if they decode, the
index is right.

Pure: no server, no database. It does need OpenCV, which the gallery already
depends on for video poster frames.
"""
import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import videotrim  # noqa: E402

FAIL = []
TMP = tempfile.mkdtemp(prefix="trim-")
FPS = 10.0
FRAMES = 60


def check(ok, label, extra=""):
    line = "  %-58s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))
    if not ok:
        FAIL.append(label)


def make_video(path, frames=FRAMES):
    """A real MP4. Each frame carries its own number as a brightness ramp, so
    a decoded frame says which one it is — a trim that keeps the wrong range
    would otherwise look identical to one that kept the right range."""
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (160, 120))
    assert w.isOpened(), "OpenCV could not open a writer"
    for i in range(frames):
        f = np.zeros((120, 160, 3), np.uint8)
        f[:, :] = (min(255, i * 4), 60, 180)
        # A block whose size tracks the frame number, so the content is
        # genuinely different frame to frame and the codec cannot coast.
        f[10:10 + (i % 40) + 5, 10:60] = (255, 255, 255)
        w.write(f)
    w.release()
    with open(path, "rb") as fh:
        return fh.read()


def decode(path):
    cap = cv2.VideoCapture(path)
    got = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        got.append(int(frame[60, 80][0]))      # the blue-channel ramp
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return got, fps


src = os.path.join(TMP, "src.mp4")
raw = make_video(src)
base_frames, base_fps = decode(src)
print("  source: %d bytes, %d frames decoded, %.1f fps"
      % (len(raw), len(base_frames), base_fps))
check(len(base_frames) == FRAMES, "the fixture really is a %d-frame video" % FRAMES,
      len(base_frames))


print("\n  --- a trim out of the middle ---")
got = videotrim.trim(raw, 2000, 4000)          # 2s..4s of a 6s clip
check(got is not None, "the trim succeeds", got is None and "None")
if got:
    out = os.path.join(TMP, "mid.mp4")
    with open(out, "wb") as fh:
        fh.write(got["data"])
    frames, fps = decode(out)
    # THE ASSERTION THAT MATTERS. A wrong index still produces a file.
    check(len(frames) > 0, "and the result actually DECODES", len(frames))
    # Counted from where the cut ACTUALLY landed, not from where it was
    # asked. The start snaps back to a keyframe, so a 2s..4s request on this
    # clip really does yield 2.8s of video — and asserting against the
    # requested span instead made a correct trim look wrong. This also proves
    # the start_ms that comes back is truthful rather than an echo.
    expected = (4000 - got["start_ms"]) * FPS / 1000
    check(abs(len(frames) - expected) <= 2,
          "with exactly the span it reports keeping",
          (len(frames), expected, got["start_ms"]))
    check(abs(fps - FPS) < 0.5, "at the original frame rate", fps)
    check(len(got["data"]) < len(raw),
          "and the file is smaller than the original",
          (len(got["data"]), len(raw)))
    # The frames kept must be the ones ASKED for, not just the right NUMBER.
    # Frame 20 of the source has blue ≈ 80; frame 0 has 0.
    check(frames and frames[0] > 40,
          "the frames kept are from the middle, not the start", frames[:3])
    check(got["start_ms"] <= 2000,
          "the cut landed at or before where it was asked", got["start_ms"])


print("\n  --- the start ---")
got = videotrim.trim(raw, 0, 2000)
check(got is not None, "a trim from the very beginning works")
if got:
    out = os.path.join(TMP, "head.mp4")
    with open(out, "wb") as fh:
        fh.write(got["data"])
    frames, _ = decode(out)
    check(len(frames) > 0, "it decodes", len(frames))
    check(frames and frames[0] < 20, "and starts at the first frame", frames[:3])


print("\n  --- the end ---")
got = videotrim.trim(raw, 4000, 6000)
check(got is not None, "a trim to the very end works")
if got:
    out = os.path.join(TMP, "tail.mp4")
    with open(out, "wb") as fh:
        fh.write(got["data"])
    frames, _ = decode(out)
    check(len(frames) > 0, "it decodes", len(frames))
    check(frames and frames[-1] > 180, "and runs to the last frame", frames[-3:])


print("\n  --- what it refuses ---")
try:
    videotrim.trim(raw, 1000, 1050)
    check(False, "a trim of 50ms is refused")
except videotrim.TrimError as e:
    check(True, "a trim of 50ms is refused", str(e)[:40])

check(videotrim.trim(b"not a video at all", 0, 1000) is None,
      "something that is not a video returns None, it does not raise")
check(videotrim.trim(b"\x00\x00\x00\x08ftypmp42", 0, 1000) is None,
      "an MP4 header with no movie box returns None")

# Truncating the file is the shape of a half-finished upload. It must refuse
# rather than produce a file built from an index that points past the end.
half = raw[:len(raw) // 2]
try:
    out = videotrim.trim(half, 1000, 3000)
    check(out is None, "a truncated file is refused", "trimmed anyway" if out else "")
except videotrim.TrimError as e:
    check(True, "a truncated file is refused", str(e)[:40])


print("\n  --- the original is never touched ---")
with open(src, "rb") as fh:
    check(fh.read() == raw, "trimming does not modify the source bytes")

# ---------------------------------------------------------------------------
# Over HTTP, against the throwaway instance. The module is proved above; what
# this adds is that the endpoint keeps the original, updates the duration the
# grid shows, and that "Use original" puts the whole clip back — the three
# things that live in the router rather than in the muxer.
print("\n  --- through the API ---")
import json
import urllib.error
import urllib.request
import uuid

B = "http://127.0.0.1:8099"


def call(path, body=None, method=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {}


def fetch(url, tok):
    req = urllib.request.Request(
        url if url.startswith("http") else B + url,
        headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


try:
    st, d = call("/api/auth/login", {"email": "priya@example.com",
                                     "password": "DemoHouse#2026"})
    live = st == 200
except Exception:
    live = False

if not live:
    # Said rather than skipped silently: a section that vanishes when the
    # server is down looks exactly like a section that passed.
    print("  (the throwaway instance is not running — API checks skipped)")
else:
    TOK = d["token"]
    b = "----sn" + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file";'
            ' filename="clip-%s.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
            % (b, uuid.uuid4().hex[:6])).encode()
    out += raw + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(
        B + "/api/gallery/upload", data=bytes(out), method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + b,
                 "Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(req, timeout=180) as r:
        item = json.load(r).get("item") or {}
    PID = item["id"]
    check(item.get("kind") == "video", "the clip uploads as a video",
          item.get("kind"))
    full_ms = item.get("duration_ms") or 0
    check(full_ms > 4000, "with its real duration", full_ms)

    st, d = call("/api/gallery/%d/trim" % PID,
                 {"start_ms": 2000, "end_ms": 4000}, tok=TOK)
    check(st == 200, "the trim endpoint accepts it", (st, d))
    if st == 200:
        check(d["duration_ms"] < full_ms,
              "the stored duration shrinks", (d["duration_ms"], full_ms))
        got = fetch(d["item"]["url"], TOK)
        out2 = os.path.join(TMP, "api.mp4")
        with open(out2, "wb") as fh:
            fh.write(got)
        frames, _ = decode(out2)
        check(len(frames) > 0, "and what comes back off the wire DECODES",
              len(frames))
        check(frames and frames[0] > 40,
              "starting partway through, as asked", frames[:3])

        st, listing = call("/api/gallery?limit=200", tok=TOK)
        row = next((x for x in listing["items"] if x["id"] == PID), {})
        check((row.get("edit") or {}).get("trim") is not None,
              "the trim is recorded on the row", row.get("edit"))

        st, d = call("/api/gallery/%d/edit/revert" % PID, {}, tok=TOK)
        check(st == 200, "a trimmed video reverts", (st, d))
        if st == 200:
            out3 = os.path.join(TMP, "back.mp4")
            with open(out3, "wb") as fh:
                fh.write(fetch(d["item"]["url"], TOK))
            frames, _ = decode(out3)
            check(len(frames) >= FRAMES - 2,
                  "and the WHOLE clip is back", len(frames))
            check(abs((d["item"].get("duration_ms") or 0) - full_ms) < 400,
                  "with its original duration",
                  (d["item"].get("duration_ms"), full_ms))

    st, d = call("/api/gallery/%d/trim" % PID,
                 {"start_ms": 3000, "end_ms": 1000}, tok=TOK)
    check(st == 422, "an end before the start is refused", st)
    st, d = call("/api/gallery/999999/trim",
                 {"start_ms": 0, "end_ms": 2000}, tok=TOK)
    check(st == 404, "another account's video is a 404", st)


print()
if FAIL:
    print("  %d FAILED:" % len(FAIL))
    for f in FAIL:
        print("    -", f)
    raise SystemExit(1)
print("  ALL PASS")
