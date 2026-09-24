"""Video operations that genuinely need a transcoder.

WHAT IS AND IS NOT IN HERE. Trimming is not: a cut changes only the index, so
`videotrim.py` does it losslessly with no dependency at all, and it stays the
default. This module is for the things that cannot avoid re-encoding — speed,
stabilisation, colour — plus one repair that has been broken since before any
of it: a poster frame for HEVC clips, which the shipped OpenCV cannot decode.

OPTIONAL, ALWAYS. `available()` is checked before anything is offered, and a
build without FFmpeg loses these operations and keeps every other one. That is
not defensive coding for its own sake: it is what makes shipping the binary a
separate decision from writing the feature, and it means the decision can be
reversed without touching this file.

LICENSING — READ THIS BEFORE SHIPPING IT.
The binary imageio-ffmpeg installs is a **GPL** build (it reports
`--enable-gpl`), not LGPL. SafeNest is sold, so that matters:

  * Invoking ffmpeg as a SEPARATE PROCESS, as this module does, is the usual
    way commercial software uses it and is widely held not to make the calling
    program a derivative work. Nothing here links against FFmpeg.
  * Distributing the binary is the part with obligations. Ship it and the GPL
    applies to IT: the source offer, the licence text, the notices.
  * An LGPL build would remove most of that, at the cost of `vidstab` — the
    stabiliser is a GPL-only filter.

The code is written so this is a packaging decision and not a code one: if the
binary is not there, `available()` is False and the app carries on.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

#: How long any one operation may run before it is killed. A stabilisation
#: pass over a long clip is genuinely slow, but an ffmpeg that has hung on a
#: malformed file would otherwise hold a worker for ever.
TIMEOUT_S = 600

#: Speed range. Below a quarter the audio filter chain needs stacking and the
#: result is a curiosity; above four it is a flicker.
SPEED_MIN, SPEED_MAX = 0.25, 4.0

_exe: str | None = None
_looked = False


def ffmpeg_path() -> str | None:
    """The bundled binary, or None. Looked up once."""
    global _exe, _looked
    if _looked:
        return _exe
    _looked = True
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        _exe = path if path and os.path.isfile(path) else None
    except Exception as exc:
        print(f"[videofx] no ffmpeg available: {exc}")
        _exe = None
    return _exe


def available() -> bool:
    return ffmpeg_path() is not None


class FxError(ValueError):
    """An operation that cannot be done, with a sentence for the person."""


def _run(args: list[str], cwd: str | None = None) -> None:
    exe = ffmpeg_path()
    if not exe:
        raise FxError("Video effects are not available in this build")
    try:
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", *args],
                           capture_output=True, timeout=TIMEOUT_S, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise FxError("That video took too long to process")
    if r.returncode != 0:
        # The last line is the useful one; the rest is configuration noise.
        tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        print(f"[videofx] ffmpeg failed: {tail[-3:] if tail else r.returncode}")
        raise FxError("That video could not be processed")


def poster(path: str, at_fraction: float = 0.1) -> bytes | None:
    """One frame as a JPEG, for a clip OpenCV cannot decode.

    THE REPAIR THIS EXISTS FOR. iPhone clips are HEVC, the shipped OpenCV
    cannot read them, and every one of them has had a placeholder tile since
    the gallery was built. This is called only after the OpenCV path has
    already failed, so nothing that works today starts paying for ffmpeg.
    """
    if not available():
        return None
    out = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    out.close()
    try:
        # Seek BEFORE -i: an input-side seek jumps by keyframe and costs
        # nothing, where an output-side one decodes every frame up to the
        # point asked for. On a long clip that is the difference between
        # instant and half a minute.
        dur = duration_s(path) or 0
        at = max(0.0, dur * at_fraction) if dur else 0.0
        _run(["-ss", f"{at:.2f}", "-i", path, "-frames:v", "1",
              "-q:v", "3", out.name])
        with open(out.name, "rb") as fh:
            data = fh.read()
        return data or None
    except FxError:
        return None
    finally:
        try:
            os.unlink(out.name)
        except OSError:
            pass


def duration_s(path: str) -> float | None:
    """Length in seconds, via ffmpeg itself — no ffprobe in this package."""
    exe = ffmpeg_path()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", path],
                           capture_output=True, timeout=60)
        text = (r.stderr or b"").decode("utf-8", "replace")
        for line in text.splitlines():
            if "Duration:" in line:
                stamp = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = stamp.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return None
    return None


def speed(src: str, dst: str, factor: float) -> None:
    """Play faster or slower. 2.0 is twice as fast.

    Video and audio need different filters for the same change, and they have
    to agree or the result drifts out of sync over a long clip — setpts scales
    the presentation times, atempo resamples the audio.
    """
    f = max(SPEED_MIN, min(SPEED_MAX, float(factor)))
    if abs(f - 1.0) < 0.01:
        raise FxError("That is the speed it already plays at")
    # atempo only accepts 0.5–2.0 per instance, so anything beyond that is
    # chained. Getting this wrong is the classic silent failure here: ffmpeg
    # rejects the filter and the caller sees a generic error.
    chain, remaining = [], f
    while remaining > 2.0:
        chain.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        chain.append("atempo=0.5")
        remaining /= 0.5
    chain.append(f"atempo={remaining:.4f}")
    _run(["-i", src,
          "-filter:v", f"setpts={1 / f:.6f}*PTS",
          "-filter:a", ",".join(chain),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-c:a", "aac", dst])


def stabilise(src: str, dst: str) -> None:
    """Take the shake out. Two passes, because one cannot see the future.

    The first pass measures the camera's motion across the whole clip and
    writes it to a file; the second applies the correction. A single-pass
    filter (deshake) exists and is much worse, because it can only react to
    motion it has already seen.

    THE MOTION FILE IS PASSED AS A BARE NAME, with ffmpeg run from the folder
    holding it. A filter argument uses ':' to separate options, so a Windows
    path — `C:/Users/...` — is read as an option break, and every escaping
    scheme for it is a fight with two layers of quoting. Changing directory
    sidesteps the whole problem: there is no colon left to misread.
    """
    work = tempfile.mkdtemp(prefix="vidstab-")
    trf = "motion.trf"
    try:
        _run(["-i", os.path.abspath(src),
              "-vf", f"vidstabdetect=shakiness=6:result={trf}",
              # `-f null -` writes to stdout and discards it. `-f null nul`
              # is the obvious Windows spelling and ffmpeg rejects it:
              # "Error opening output file nul."
              "-f", "null", "-"], cwd=work)
        _run(["-i", os.path.abspath(src),
              "-vf", f"vidstabtransform=input={trf}:smoothing=24"
                     ":optzoom=1,unsharp=5:5:0.8:3:3:0.4",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-c:a", "copy", os.path.abspath(dst)], cwd=work)
    finally:
        try:
            os.unlink(os.path.join(work, trf))
            os.rmdir(work)
        except OSError:
            pass


#: Colour looks, as ffmpeg filter chains. Deliberately the same NAMES the
#: photo editor uses, so "vivid" means the same thing to somebody whichever
#: kind of item they are looking at.
FILTERS = {
    "mono": "hue=s=0",
    "noir": "hue=s=0,eq=contrast=1.35",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    "vivid": "eq=saturation=1.45",
    "fade": "eq=contrast=0.75:brightness=0.05",
    "warm": "colorchannelmixer=1.08:0:0:0:0:1:0:0:0:0:0.93",
    "cool": "colorchannelmixer=0.93:0:0:0:0:1:0:0:0:0:1.08",
}


def colour(src: str, dst: str, name: str, brightness: float = 1.0,
           contrast: float = 1.0, saturation: float = 1.0) -> None:
    """A named look and the three adjustments, in one pass."""
    parts = []
    if name and name != "none":
        if name not in FILTERS:
            raise FxError(f"There is no filter called {name!r}")
        parts.append(FILTERS[name])
    eq = []
    # ffmpeg's brightness is an OFFSET from -1 to 1, not a multiplier like
    # PIL's. Passing the photo editor's 0.5–2.0 straight through would black
    # the clip out at one end and blow it out at the other.
    if abs(brightness - 1.0) > 0.01:
        eq.append(f"brightness={max(-1.0, min(1.0, brightness - 1.0)):.3f}")
    if abs(contrast - 1.0) > 0.01:
        eq.append(f"contrast={max(0.0, min(3.0, contrast)):.3f}")
    if abs(saturation - 1.0) > 0.01:
        eq.append(f"saturation={max(0.0, min(3.0, saturation)):.3f}")
    if eq:
        parts.append("eq=" + ":".join(eq))
    if not parts:
        raise FxError("Nothing was asked for")
    _run(["-i", src, "-vf", ",".join(parts),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
          "-c:a", "copy", dst])


# _esc was here: it escaped a Windows path for use inside a filter argument
# and ffmpeg rejected the result anyway. Passing a bare filename with a
# working directory is what actually works, so there is nothing left to
# escape — see stabilise().
