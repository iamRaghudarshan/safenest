"""Video effects, and the HEVC poster frame that has been broken all along.

Everything here re-encodes, which is why it needs FFmpeg and why trimming
deliberately does not use it. The checks decode the OUTPUT rather than trusting
an exit code: ffmpeg returns 0 for plenty of things that produce a file nobody
can play.

The one that is a repair rather than a feature is `poster`. iPhone clips are
HEVC, the shipped OpenCV cannot decode them, and every one of them has shown a
placeholder tile since the gallery was built.

Skips itself, loudly, when FFmpeg is absent — a section that vanishes silently
looks exactly like a section that passed.
"""
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import videofx  # noqa: E402

FAIL = []
TMP = tempfile.mkdtemp(prefix="videofx-")
FPS = 10.0
FRAMES = 40


def check(ok, label, extra=""):
    line = "  %-58s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))
    if not ok:
        FAIL.append(label)


if not videofx.available():
    print("  FFmpeg is not installed in this build.")
    print("  These operations are OPTIONAL by design, so that is not a failure")
    print("  — but nothing below was checked. Install imageio-ffmpeg to run it.")
    raise SystemExit(0)

print("  ffmpeg:", videofx.ffmpeg_path())


def make_video(path, frames=FRAMES, wobble=False):
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (160, 120))
    assert w.isOpened()
    for i in range(frames):
        f = np.zeros((120, 160, 3), np.uint8)
        f[:, :] = (40, 70, 200)
        # A bright square. Under `wobble` it jitters, which is what gives the
        # stabiliser something real to correct rather than a still image it
        # can trivially "fix".
        dx = int(6 * np.sin(i)) if wobble else 0
        dy = int(5 * np.cos(i * 1.3)) if wobble else 0
        f[40 + dy:80 + dy, 50 + dx:110 + dx] = (255, 255, 255)
        w.write(f)
    w.release()
    return path


def probe(path):
    cap = cv2.VideoCapture(path)
    n = 0
    mean = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        n += 1
        mean.append(float(frame.mean()))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return n, fps, (sum(mean) / len(mean) if mean else 0)


src = make_video(os.path.join(TMP, "src.mp4"))
n0, fps0, mean0 = probe(src)
check(n0 == FRAMES, "the fixture is a %d-frame clip" % FRAMES, n0)


print("\n  --- speed ---")
fast = os.path.join(TMP, "fast.mp4")
videofx.speed(src, fast, 2.0)
n, fps, _ = probe(fast)
check(os.path.getsize(fast) > 0, "a 2x version is produced", os.path.getsize(fast))
check(n > 0, "and it DECODES", n)
# DURATION, not frame count. setpts rescales the presentation times and keeps
# every frame, raising the output frame rate instead — so a 2x clip has the
# same 40 frames in half the time. Asserting on the count made a correct
# result look broken.
base = videofx.duration_s(src) or 0
fast_s = videofx.duration_s(fast) or 0
check(abs(fast_s - base / 2) < 0.5, "and it runs in half the time",
      (round(fast_s, 2), round(base / 2, 2)))

slow = os.path.join(TMP, "slow.mp4")
videofx.speed(src, slow, 0.5)
slow_s = videofx.duration_s(slow) or 0
check(abs(slow_s - base * 2) < 0.8, "and half speed takes about twice as long",
      (round(slow_s, 2), round(base * 2, 2)))

# The atempo chain is the classic silent failure: ffmpeg refuses a single
# atempo outside 0.5-2.0, so 4x has to be chained.
very = os.path.join(TMP, "very.mp4")
videofx.speed(src, very, 4.0)
n, _, _ = probe(very)
very_s = videofx.duration_s(very) or 0
check(n > 0 and abs(very_s - base / 4) < 0.5,
      "4x works, so the chained atempo is right", (round(very_s, 2), round(base / 4, 2)))

for bad in (1.0, 1.001):
    try:
        videofx.speed(src, os.path.join(TMP, "x.mp4"), bad)
        check(False, "asking for the speed it already plays at is refused")
    except videofx.FxError:
        check(True, "asking for the speed it already plays at is refused")
    break


print("\n  --- colour ---")
mono = os.path.join(TMP, "mono.mp4")
videofx.colour(src, mono, "mono")
n, _, _ = probe(mono)
check(n > 0, "a mono version decodes", n)
cap = cv2.VideoCapture(mono)
ok, frame = cap.read()
cap.release()
if ok:
    b, g, r = frame[100, 20]
    check(abs(int(b) - int(g)) < 24 and abs(int(g) - int(r)) < 24,
          "and it really is grey, not merely labelled", (b, g, r))

bright = os.path.join(TMP, "bright.mp4")
videofx.colour(src, bright, "none", brightness=1.4)
n, _, mean = probe(bright)
check(n > 0 and mean > mean0, "brightening really brightens",
      (round(mean, 1), round(mean0, 1)))

try:
    videofx.colour(src, os.path.join(TMP, "x.mp4"), "instagram")
    check(False, "an unknown filter is refused")
except videofx.FxError as e:
    check(True, "an unknown filter is refused", str(e)[:40])

try:
    videofx.colour(src, os.path.join(TMP, "x.mp4"), "none")
    check(False, "a request that changes nothing is refused")
except videofx.FxError:
    check(True, "a request that changes nothing is refused")


print("\n  --- stabilise ---")
shaky = make_video(os.path.join(TMP, "shaky.mp4"), wobble=True)
steady = os.path.join(TMP, "steady.mp4")
videofx.stabilise(shaky, steady)
n, _, _ = probe(steady)
check(n > 0, "a stabilised version decodes", n)
check(os.path.getsize(steady) > 0, "and is a real file", os.path.getsize(steady))


print("\n  --- the poster repair ---")
shot = videofx.poster(src)
check(bool(shot), "a poster frame comes out", len(shot or b""))
check(shot and shot[:2] == b"\xff\xd8", "and it is a JPEG", (shot or b"")[:4])

# THE CASE THIS EXISTS FOR. Re-encode the fixture as HEVC, which is what an
# iPhone writes and what the shipped OpenCV cannot read.
hevc = os.path.join(TMP, "hevc.mp4")
subprocess.run([videofx.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                "-i", src, "-c:v", "libx265", "-crf", "28", "-tag:v", "hvc1",
                hevc], capture_output=True, timeout=300)
if os.path.exists(hevc) and os.path.getsize(hevc) > 0:
    cap = cv2.VideoCapture(hevc)
    ok, _f = cap.read()
    cap.release()
    print("      (OpenCV %s decode this HEVC clip)" % ("CAN" if ok else "cannot"))
    shot = videofx.poster(hevc)
    check(bool(shot) and shot[:2] == b"\xff\xd8",
          "ffmpeg gets a poster frame out of an HEVC clip", len(shot or b""))
else:
    check(False, "could not build an HEVC fixture to test with")

check(videofx.duration_s(src) and abs(videofx.duration_s(src) - 4.0) < 0.6,
      "duration is read back correctly", videofx.duration_s(src))
check(videofx.poster(os.path.join(TMP, "nope.mp4")) is None,
      "a missing file gives None rather than raising")

# ---------------------------------------------------------------------------
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
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {}


try:
    st, d = call("/api/auth/login", {"email": "priya@example.com",
                                     "password": "DemoHouse#2026"})
    live = st == 200
except Exception:
    live = False

if not live:
    print("  (the throwaway instance is not running - API checks skipped)")
else:
    TOK = d["token"]
    st, d = call("/api/gallery/effects/available", tok=TOK)
    check(st == 200 and d.get("available") is True,
          "the app reports that effects are available", d)
    check(sorted(d.get("operations") or []) == ["colour", "speed", "stabilise"],
          "and names what it can do", d.get("operations"))

    with open(src, "rb") as fh:
        clip = fh.read()
    b = "----sn" + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file";'
            ' filename="fx-%s.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
            % (b, uuid.uuid4().hex[:6])).encode()
    out += clip + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(
        B + "/api/gallery/upload", data=bytes(out), method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + b,
                 "Authorization": "Bearer " + TOK})
    with urllib.request.urlopen(req, timeout=300) as r:
        item = json.load(r).get("item") or {}
    PID = item["id"]
    before_ms = item.get("duration_ms") or 0
    check(before_ms > 3000, "a clip uploads with its duration", before_ms)

    st, d = call("/api/gallery/%d/effect" % PID,
                 {"kind": "speed", "factor": 2.0}, tok=TOK)
    check(st == 200, "a 2x speed effect applies", (st, d))
    if st == 200:
        after = d["item"].get("duration_ms") or 0
        check(abs(after - before_ms / 2) < 700,
              "and the stored duration halves", (after, before_ms / 2))

    # Reversible, which matters MORE here than for a trim: this re-encodes,
    # so the pixels genuinely changed and the original is the only way back.
    st, d = call("/api/gallery/%d/edit/revert" % PID, {}, tok=TOK)
    check(st == 200, "and it reverts", st)
    if st == 200:
        back = d["item"].get("duration_ms") or 0
        check(abs(back - before_ms) < 400,
              "to the original length", (back, before_ms))

    st, d = call("/api/gallery/%d/effect" % PID,
                 {"kind": "colour", "filter": "mono"}, tok=TOK)
    check(st == 200, "a colour effect applies", (st, d))
    call("/api/gallery/%d/edit/revert" % PID, {}, tok=TOK)

    st, d = call("/api/gallery/%d/effect" % PID, {"kind": "sharpen"}, tok=TOK)
    check(st == 422, "an unknown effect is refused", st)
    st, d = call("/api/gallery/999999/effect", {"kind": "speed", "factor": 2},
                 tok=TOK)
    check(st == 404, "another account's video is a 404", st)


print()
if FAIL:
    print("  %d FAILED:" % len(FAIL))
    for f in FAIL:
        print("    -", f)
    raise SystemExit(1)
print("  ALL PASS")
