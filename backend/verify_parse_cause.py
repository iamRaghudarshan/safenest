"""Does a body FastAPI cannot parse now say WHY, in the log?

Four uploads failed in the same second with "There was an error parsing the
body" and that sentence names nothing: a malformed boundary, a client that
hung up mid-part and a part-size limit all produce it, and they want
different fixes. FastAPI raises it `from e`, so the real exception exists for
a moment and is then discarded when it becomes a response.

This proves the cause reaches the log, and — the part worth guarding — that
ordinary errors still come back to the client exactly as they did, because
registering a handler for StarletteHTTPException takes over EVERY
HTTPException in the application, not just this one.

Run:  backend/venv/Scripts/python.exe backend/verify_parse_cause.py
"""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.main import UPLOAD_LOG, _http_exception_with_cause  # noqa: E402

ok = True


def check(name, passed, detail=""):
    global ok
    ok = ok and passed
    print("%s  %s%s" % ("PASS" if passed else "FAIL", name,
                        ("  — " + detail) if detail else ""))


def fake_request(path: str) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "root_path": "", "scheme": "http",
        "server": ("test", 80), "client": ("test", 1), "http_version": "1.1",
    })


def tail() -> str:
    if not os.path.exists(UPLOAD_LOG):
        return ""
    with io.open(UPLOAD_LOG, encoding="utf-8", errors="replace") as f:
        return f.read()[-1200:]


before = tail()

# The shape FastAPI actually produces.
try:
    raise ValueError("Did not find boundary character 45 at index 2")
except ValueError as real:
    swallowed = StarletteHTTPException(
        status_code=400, detail="There was an error parsing the body")
    swallowed.__cause__ = real

print("== 1. the cause is written down ==")
resp = asyncio.run(_http_exception_with_cause(
    fake_request("/api/gallery/upload"), swallowed))
after = tail()
check("the real exception type is in the log", "ValueError" in after[len(before):]
      or "ValueError" in after)
check("and its message, which is the actionable half",
      "Did not find boundary character" in after)

print()
print("== 2. the client is told no more than before ==")
# The cause can name internals — paths, limits, library versions. It belongs
# in a file on this machine, not in a reply to a phone.
body = bytes(resp.body).decode()
check("status is unchanged", resp.status_code == 400, str(resp.status_code))
check("the reply still says only the safe sentence",
      "There was an error parsing the body" in body, body[:80])
check("and does NOT leak the cause to the caller",
      "boundary character" not in body)

print()
print("== 3. every OTHER HTTPException still behaves ==")
# This handler is registered for StarletteHTTPException, so it intercepts the
# whole application's 401s, 403s and 404s too. If it changed any of them it
# would be a far bigger bug than the one it was added for.
for code, detail, path in [
    (401, "Not authenticated", "/api/gallery/list"),
    (404, "Not found", "/api/anything"),
    (413, "File too large", "/api/gallery/upload/chunk"),
]:
    r = asyncio.run(_http_exception_with_cause(
        fake_request(path), StarletteHTTPException(status_code=code,
                                                   detail=detail)))
    txt = bytes(r.body).decode()
    check("%d %s passes through unchanged" % (code, detail),
          r.status_code == code and detail in txt, txt[:60])

print()
print("== 4. a failure with no cause does not crash the handler ==")
# `__cause__` is None for every HTTPException raised deliberately, which is
# nearly all of them — and this runs on the error path, where an exception
# would replace a clear 400 with a 500.
r = asyncio.run(_http_exception_with_cause(
    fake_request("/api/gallery/upload"),
    StarletteHTTPException(status_code=400, detail="Empty file")))
check("still answers", r.status_code == 400 and "Empty file" in bytes(r.body).decode())

print()
print("ALL PASS" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
