"""Refuse to seed anything that is not the throwaway instance.

This exists because it nearly went wrong. The demo instance and an unrelated
preview proxy were both configured for port 8099; the proxy got there first, so
uvicorn failed to bind with "only one usage of each socket address" and quietly
shut down -- and that proxy forwards /api/* to the PRODUCTION server on 8080.
The seed scripts then pointed demo data straight at the real database. They
stopped only because the demo password did not exist there, which is luck, not
a safeguard.

Port checks are not enough on their own: something answering on the expected
port is exactly the failure above. So this proves the HTTP server is backed by
the demo SQLite FILE, by reading the account out of that file directly and
requiring the API to report the same id and email back.

Call assert_throwaway(BASE, token) before the first write in any seeder.
"""
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

DEMO_DB = Path(os.path.expandvars(r"%TEMP%\safenest-demo\demo.db"))


class NotTheThrowaway(SystemExit):
    pass


def _fail(msg):
    raise NotTheThrowaway(
        "REFUSING TO SEED: " + msg +
        "\n  Start the throwaway instance with demo_env.bat and make sure nothing"
        "\n  else is holding its port. Never point these scripts at 8080."
    )


def demo_account():
    """The single account inside the throwaway database."""
    if not DEMO_DB.exists():
        _fail(f"no demo database at {DEMO_DB}")
    con = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, email FROM users ORDER BY id").fetchall()
    finally:
        con.close()
    if len(rows) != 1:
        _fail(f"demo database holds {len(rows)} accounts, expected exactly 1 — "
              "this may not be a throwaway database")
    return rows[0]


def assert_throwaway(base: str, token: str) -> None:
    """Prove the server at `base` is backed by the demo SQLite file."""
    want_id, want_email = demo_account()

    req = urllib.request.Request(base + "/api/auth/me",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        me = json.load(r)
    me = me.get("user", me)
    got_id, got_email = me.get("id"), (me.get("email") or "").lower()

    if got_id != want_id or got_email != want_email.lower():
        _fail(f"the server at {base} reports account {got_id}/{got_email!r}, but the "
              f"demo database holds {want_id}/{want_email!r} — the port is being "
              "answered by a DIFFERENT instance")

    print(f"  guard: {base} is the throwaway instance "
          f"(account {got_id} {got_email})")
