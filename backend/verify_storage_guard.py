"""Checks on the records-folder guard and the recovery route it feeds.

Written because the bug this fixes was invisible to every existing test: the
"is your records drive connected?" check used Path.anchor, which is "E:\\" on
Windows and "/" on macOS. It worked where it was written and was a no-op on the
platform the customer was using, so a failing USB drive produced a licence error
and a wall of 500s instead of one sentence naming the drive.

Run:  venv/Scripts/python.exe verify_storage_guard.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "packaging"))

OK, BAD = 0, 0


def check(name, cond, extra=""):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        BAD += 1
        print(f"  FAIL {name} {extra}")


print("\n-- the bug itself: which volume does a path sit on")
import runner

# The regression. On macOS every path's anchor is "/", so the old check could
# never fail there. _volume_of must name the mounted volume, not the root.
mac = Path("/Volumes/SAFENEST/SafeNest/data")
vol = runner._volume_of(mac)
check("a /Volumes path resolves to its volume, not the filesystem root",
      "SAFENEST" in str(vol), f"got {vol!r}")
check("and that is NOT the anchor, which is what the bug used",
      str(vol) != mac.anchor, f"anchor={mac.anchor!r} volume={vol!r}")
check("PurePosixPath agrees the old check was a no-op on macOS",
      PurePosixPath(mac).anchor == "/")
check("while on Windows the anchor was right, which is why it was never noticed",
      PureWindowsPath("E:/SafeNest/data").anchor == "E:\\")
check("a linux /media/<user>/<name> path resolves to the volume",
      "STICK" in str(runner._volume_of(Path("/media/raghu/STICK/data"))))
check("an ordinary internal path is left alone",
      runner._volume_of(Path.home() / "records") is not None)

print("\n-- probing a folder for whether it can actually hold records")
with tempfile.TemporaryDirectory() as tmp:
    good = Path(tmp) / "records"
    good.mkdir()
    check("a working folder probes clean", runner._probe(good) is None)
    check("the probe leaves nothing behind",
          not (good / ".write-test").exists())

    gone = Path(tmp) / "not-there" / "records"
    p = runner._probe(gone)
    # Not on a named volume, so it is created rather than reported missing --
    # this is the ordinary "first run" case and must not be treated as a fault.
    check("a missing ordinary folder is created, not called a fault", p is None)

# EXISTING IS NOT WORKING. This is the customer's drive exactly: mounted, listed
# by the OS, and every read under it returns EIO.
real_listdir = os.listdir


def erroring_listdir(path):
    if "SAFENEST" in str(path):
        raise OSError(5, "Input/output error")
    return real_listdir(path)


with tempfile.TemporaryDirectory() as tmp:
    faulty = Path(tmp) / "SAFENEST" / "data"
    faulty.mkdir(parents=True)
    os.listdir = erroring_listdir
    try:
        p = runner._probe(faulty)
    finally:
        os.listdir = real_listdir
    check("a folder that exists but errors on read is reported unreadable",
          p is not None and p.get("reason") == "unreadable", f"got {p}")
    check("and the fault carries the folder, so the message can name it",
          p is not None and "SAFENEST" in p.get("folder", ""))

print("\n-- what the customer is told")
from app.main import _storage_sentence

s = _storage_sentence({"reason": "unreadable", "volume": "/Volumes/SAFENEST"})
check("the sentence names the drive", "SAFENEST" in s)
check("and says outright that nothing was deleted", "deleted" in s.lower())
check("missing reads differently from unreadable",
      _storage_sentence({"reason": "missing", "volume": "/Volumes/X"}) != s)
check("a readonly volume gets its own sentence",
      "written" in _storage_sentence({"reason": "readonly", "volume": "/Volumes/X"}))

print("\n-- the gate")
from fastapi.testclient import TestClient
from app.main import app

os.environ.pop("SAFENEST_STORAGE_PROBLEM", None)
c = TestClient(app, raise_server_exceptions=False)
r = c.get("/api/health")
check("with no fault the gate is inert", r.status_code == 200, r.status_code)

os.environ["SAFENEST_STORAGE_PROBLEM"] = json.dumps({
    "reason": "unreadable", "volume": "/Volumes/SAFENEST",
    "folder": "/Volumes/SAFENEST/SafeNest/data",
    "pointer": "/tmp/data-location.txt"})

r = c.get("/api/expenses")
check("an ordinary call is refused with 503, not 500", r.status_code == 503, r.status_code)
body = r.json()
check("the refusal explains itself in words",
      "SAFENEST" in body.get("detail", ""), body.get("detail", "")[:60])
check("and carries the fault for the screen to render",
      body.get("storage", {}).get("reason") == "unreadable")

r = c.get("/api/health")
check("health still answers, so the shell knows the server is alive",
      r.status_code == 200, r.status_code)
r = c.get("/api/storage/problem")
check("the recovery route is reachable while the gate is closed",
      r.status_code == 200, r.status_code)
check("and reports the fault", r.json().get("reason") == "unreadable")

# The whole point of ordering. A folder that cannot be read cannot yield a
# licence either, and being told the licence is missing sends the owner chasing
# a licence that is fine -- on the very drive that is the actual fault.
mw = [m.kwargs.get("dispatch").__name__ for m in app.user_middleware
      if m.kwargs.get("dispatch")]
check("storage is answered before licence", mw.index("storage_gate") < mw.index("licence_gate"),
      str(mw))

print("\n-- repointing is a local act")
from app.routers import storage as st


class FakeClient:
    def __init__(self, host): self.host = host


class FakeReq:
    def __init__(self, host): self.client = FakeClient(host)


from fastapi import HTTPException

try:
    st._at_the_machine(FakeReq("192.168.1.50"))
    check("a LAN client cannot repoint the records", False)
except HTTPException as e:
    check("a LAN client cannot repoint the records", e.status_code == 403)
try:
    st._at_the_machine(FakeReq("127.0.0.1"))
    check("someone at the machine can", True)
except HTTPException:
    check("someone at the machine can", False)

with tempfile.TemporaryDirectory() as tmp:
    pointer = Path(tmp) / "data-location.txt"
    pointer.write_text("/Volumes/SAFENEST/SafeNest/data\n", encoding="utf-8")
    os.environ["SAFENEST_STORAGE_PROBLEM"] = json.dumps({
        "reason": "unreadable", "folder": "/Volumes/SAFENEST/SafeNest/data",
        "pointer": str(pointer)})
    out = st.use_local(FakeReq("127.0.0.1"))
    check("use-local asks for a restart, because the engine binds at startup",
          out.get("restart_required") is True)
    check("THE ONE THAT MATTERS: the pointer is moved, never deleted",
          Path(out["kept_at"]).is_file() and not pointer.exists())
    check("and the moved file still holds where the records really are",
          "SAFENEST" in Path(out["kept_at"]).read_text(encoding="utf-8"))

os.environ.pop("SAFENEST_STORAGE_PROBLEM", None)

print("")
print("-- self-repair: reinstall the new version and it sorts itself out")
import runner as _r

with tempfile.TemporaryDirectory() as tmp:
    used = Path(tmp) / "used"
    used.mkdir()
    (used / "instance.env").write_text("x", encoding="utf-8")
    (used / "finmate.db").write_text("x", encoding="utf-8")
    check("a folder that has been used is recognised", _r._has_records(used))

    fresh = Path(tmp) / "fresh"
    fresh.mkdir()
    (fresh / "finmate.db").write_text("x", encoding="utf-8")
    # A licensed bundle ships an empty finmate.db carrying the branding, so the
    # database alone answers yes for a copy straight out of the zip.
    check("a shipped-but-never-used folder is NOT mistaken for records",
          not _r._has_records(fresh))
    check("an empty folder is not either", not _r._has_records(Path(tmp) / "nope"))

# The behaviour the owner asked for: reinstall, and it opens.
os.environ["SAFENEST_STORAGE_PROBLEM"] = json.dumps({
    "reason": "unreadable", "mode": "fallback", "volume": "/Volumes/SAFENEST",
    "folder": "/Volumes/SAFENEST/SafeNest/data"})
r = c.get("/api/health")
check("in fallback the app is NOT gated", r.status_code == 200, r.status_code)
r = c.get("/api/storage/problem")
check("but the fault is still there to be shown", r.json().get("mode") == "fallback")
s = _storage_sentence({"reason": "unreadable", "mode": "fallback",
                       "volume": "/Volumes/SAFENEST"})
check("and the banner says where changes are going",
      "stays here" in s and "SAFENEST" in s, s[:70])

# The case that must still stop. An empty app shown to somebody with years of
# records is indistinguishable from having lost them, and that guess is what
# makes people reinstall and reformat.
os.environ["SAFENEST_STORAGE_PROBLEM"] = json.dumps({
    "reason": "unreadable", "mode": "blocked", "volume": "/Volumes/SAFENEST"})
r = c.get("/api/expenses")
check("THE ONE THAT MATTERS: with nothing to fall back to it still stops",
      r.status_code == 503, r.status_code)

os.environ.pop("SAFENEST_STORAGE_PROBLEM", None)

print(f"\n{OK} passed, {BAD} failed\n")
sys.exit(1 if BAD else 0)
