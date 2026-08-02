"""Licences for copies of the app handed to other people.

Two halves live in this one file because they must agree exactly:

  * the publisher side — signs a licence with a private key that never leaves the
    machine that issues it;
  * the customer side — verifies that signature against a public key baked into
    the build, checks the expiry, and asks the publisher whether the licence has
    been revoked since.

Ed25519 rather than a shared secret: a symmetric key would have to ship inside
every customer's copy, and anyone holding it could mint their own licences.

WHAT THIS DOES AND DOES NOT DO
The licence runs on hardware the customer controls. They own the clock, the
binary and the disk, so a determined person can defeat any of this. What it does
buy: an ordinary user cannot keep using an expired copy, cannot promote
themselves to admin by editing the database (the role is inside the signature),
and cannot pass a working copy to someone else without also passing a licence
that names them. That is the honest bar — inconvenience, not prevention.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
from datetime import date, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from . import ist

PREFIX = "FINMATE-1"
GRACE_DAYS = 3          # after expiry, still opens read-only so data can be exported

OK = "ok"
EXPIRING = "expiring"   # valid, but worth warning about
GRACE = "grace"         # expired, inside the export window
EXPIRED = "expired"
REVOKED = "revoked"
INVALID = "invalid"     # signature failed, or the file is not a licence
MISSING = "missing"

BLOCKING = {EXPIRED, REVOKED, INVALID, MISSING}


# --------------------------------------------------------------------- encoding
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _canonical(payload: dict) -> bytes:
    """The exact bytes that get signed.

    Sorted keys and no incidental whitespace: the verifier re-serialises the
    payload it parsed, and any difference in ordering or spacing would look like
    a forged signature.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ------------------------------------------------------------ publisher: keys
def new_keypair() -> tuple[str, str]:
    """(private_hex, public_hex). Run once; keep the private half secret."""
    private = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization as ser
    raw_priv = private.private_bytes(
        encoding=ser.Encoding.Raw, format=ser.PrivateFormat.Raw,
        encryption_algorithm=ser.NoEncryption())
    raw_pub = private.public_key().public_bytes(
        encoding=ser.Encoding.Raw, format=ser.PublicFormat.Raw)
    return raw_priv.hex(), raw_pub.hex()


def _private_key(hex_key: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_key))


def _public_key(hex_key: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key))


def new_key_id() -> str:
    """Short, unmistakable, and safe in a URL — it goes in the revocation path."""
    return "L-" + secrets.token_hex(4).upper()


# ------------------------------------------------------------ publisher: issue
def issue(private_key_hex: str, *, key_id: str, name: str, email: str,
          days: int | None, role: str = "user", modules: list[str] | None = None,
          issuer: str = "", note: str = "", seats: int = 1) -> tuple[str, dict]:
    """Sign a licence. Returns (token, payload).

    `days` counts from today, so a 30-day licence issued today expires 30 days
    from today — not 30 days from whenever the customer first opens the app. The
    alternative (start on first run) needs the customer's copy to report back,
    which is exactly the dependency this design avoids.

    `days=None` issues a perpetual licence. It carries an explicit `perpetual`
    flag rather than simply omitting `expires`: a token that has merely *lost* its
    expiry date must stay INVALID, and telling "sold outright" apart from "damaged"
    is not something to infer from an absence.

    `seats` is how many sign-ins the household may have — the licence holder plus
    their family. 0 means unlimited.
    """
    if days is not None and days < 1:
        raise ValueError("A licence must be valid for at least one day")
    if seats < 0:
        raise ValueError("Seats cannot be negative")
    today = ist.today()
    payload = {
        "kid": key_id,
        "name": name.strip(),
        "email": email.strip().lower(),
        "role": role,
        "issued": today.isoformat(),
        "issuer": (issuer or "").rstrip("/"),
        "seats": int(seats),
    }
    if days is None:
        payload["perpetual"] = True
    else:
        payload["expires"] = (today + timedelta(days=int(days))).isoformat()
    if modules:
        payload["modules"] = sorted(modules)
    if note:
        payload["note"] = note[:200]
    signature = _private_key(private_key_hex).sign(_canonical(payload))
    return f"{PREFIX}.{_b64(_canonical(payload))}.{_b64(signature)}", payload


# ------------------------------------------------------------ customer: verify
def parse(token: str, public_key_hex: str) -> dict:
    """Decode and check a licence token.

    Returns the payload with a "state" added. Never raises for bad input — an
    unreadable licence is a state to display, not a crash on startup.
    """
    if not token or not token.strip():
        return {"state": MISSING}
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        # app_name() opens its own session and swallows every failure, which
        # matters here: this runs at boot, before anyone can be sure the database
        # is answering, and an unreadable licence must stay a state to display.
        from .routers.branding import app_name
        return {"state": INVALID, "reason": f"This is not a {app_name()} licence."}
    try:
        raw = _unb64(parts[1])
        payload = json.loads(raw)
        _public_key(public_key_hex).verify(_unb64(parts[2]), _canonical(payload))
    except InvalidSignature:
        return {"state": INVALID, "reason": "The licence signature does not match."}
    except Exception:
        return {"state": INVALID, "reason": "The licence file is damaged."}

    # One of the two must be present. A perpetual licence has no expiry date by
    # design; a damaged one has neither, and is still rejected. The signature has
    # already been checked, so `perpetual` here cannot be something the holder
    # added themselves.
    if not isinstance(payload, dict) or not (payload.get("expires")
                                             or payload.get("perpetual")):
        return {"state": INVALID, "reason": "The licence is missing its expiry."}
    return {**payload, "state": OK}


def evaluate(payload: dict, *, revoked: bool = False, today: date | None = None,
             warn_within: int = 7) -> dict:
    """Turn a verified payload into a state the app can act on."""
    if payload.get("state") in (MISSING, INVALID):
        return payload
    if revoked:
        return {**payload, "state": REVOKED,
                "reason": "This licence has been withdrawn by the supplier."}

    today = today or ist.today()

    # Sold outright: no expiry to compare against, so none of the date arithmetic
    # below applies. Revocation is still checked above — a perpetual licence can
    # be withdrawn, it just never lapses on its own.
    if payload.get("perpetual"):
        return {**payload, "state": OK, "days_left": None, "expires_on": None}

    try:
        expires = date.fromisoformat(payload["expires"])
    except (KeyError, TypeError, ValueError):
        return {**payload, "state": INVALID, "reason": "The expiry date is unreadable."}

    left = (expires - today).days
    out = {**payload, "days_left": left,
           "expires_on": ist.fmt(expires, with_time=False)}
    if left < -GRACE_DAYS:
        return {**out, "state": EXPIRED,
                "reason": f"This licence expired on {out['expires_on']}."}
    if left < 0:
        return {**out, "state": GRACE,
                "reason": (f"This licence expired on {out['expires_on']}. You can still "
                           f"read and export your data for {GRACE_DAYS + left} more day(s).")}
    if left <= warn_within:
        return {**out, "state": EXPIRING,
                "reason": (f"This licence expires in {left} day(s), on {out['expires_on']}."
                           if left else "This licence expires today.")}
    return {**out, "state": OK}


def is_blocked(state: str) -> bool:
    return state in BLOCKING


# 0 means unlimited, everywhere this appears.
UNLIMITED_SEATS = 0


def seats_allowed(payload: dict) -> int:
    """How many sign-ins this licence permits. 0 = unlimited.

    A licence issued before seats existed has no `seats` key, and defaults to 1 —
    which is exactly what those copies can do today, since only the licence holder
    has an account and a customer copy could not create more. Defaulting to
    unlimited would silently widen every licence already in the field.
    """
    raw = payload.get("seats")
    if raw is None:
        return 1
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(0, n)


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value: str) -> bool:
    return bool(EMAIL.match((value or "").strip()))


# ---------------------------------------------------------------- customer side
CHECK_EVERY_HOURS = 24
_runtime: dict = {"checked_at": None, "state": None}


def _sidecar(path):
    """Where the last revocation answer is remembered, next to the licence."""
    return path.with_name(path.name + ".check")


def read_token(path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def install_token(path, token: str) -> None:
    """Write a licence into place, replacing whatever was there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    _sidecar(path).unlink(missing_ok=True)     # the old answer was about another licence
    _runtime.update({"checked_at": None, "state": None})


def _cached_revocation(path) -> dict:
    try:
        return json.loads(_sidecar(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _remember_revocation(path, kid: str, revoked: bool) -> None:
    try:
        _sidecar(path).write_text(json.dumps(
            {"kid": kid, "revoked": bool(revoked), "at": ist.now().isoformat()}),
            encoding="utf-8")
    except OSError:
        pass                                    # a read-only disk must not stop the app


def about_this_copy() -> dict:
    """What a copy tells its supplier about itself.

    Kept to four facts, all about the machine and the build — never about the
    records. This exact dictionary is also what the copy shows its own owner, so
    the list cannot quietly grow beyond what was disclosed.
    """
    import platform as _p
    import socket as _s
    system = _p.system().lower()
    return {
        "platform": "mac" if system == "darwin" else ("windows" if system == "windows"
                                                      else "linux"),
        "os": (f"macOS {_p.mac_ver()[0]}" if system == "darwin"
               else f"{_p.system()} {_p.release()}")[:120],
        "version": "2.0",
        "host": _s.gethostname()[:120],
    }


def check_revoked(kid: str, issuer: str, path, timeout: float = 6.0) -> bool:
    """Has the supplier withdrawn this licence?

    Deliberately fails open. The customer's internet being down, or the
    publisher's laptop being asleep, must not brick a copy that holds a valid
    signature and an unexpired date — that would hand every outage to the
    customer as a broken app. The offline expiry is still enforced regardless,
    so failing open costs at most the remaining days of a licence being pulled
    early.
    """
    remembered = _cached_revocation(path)
    if not issuer or not kid:
        return bool(remembered.get("revoked"))

    import requests
    try:
        r = requests.get(f"{issuer.rstrip('/')}/api/licence/check/{kid}",
                         params=about_this_copy(), timeout=timeout)
        if r.status_code == 200:
            revoked = bool(r.json().get("revoked"))
            _remember_revocation(path, kid, revoked)
            return revoked
    except Exception:
        pass
    # Unreachable — trust the last answer we did get, if it was about this licence.
    return bool(remembered.get("revoked")) if remembered.get("kid") == kid else False


def _due_for_check(path, kid: str) -> bool:
    remembered = _cached_revocation(path)
    if remembered.get("kid") != kid:
        return True
    if remembered.get("revoked"):
        return False                            # already withdrawn; nothing to re-ask
    try:
        seen = datetime.fromisoformat(remembered["at"])
        return (ist.now() - seen).total_seconds() > CHECK_EVERY_HOURS * 3600
    except Exception:
        return True


def status(path, public_key_hex: str, issuer: str = "",
           allow_network: bool = False, force: bool = False) -> dict:
    """The licence state this copy should act on.

    `allow_network` is off by default and every request path leaves it off. A
    revocation check that reaches over the internet belongs on a background
    thread — put it inline and one unreachable publisher turns every single
    request into a six-second wait, which is a far worse failure than the licence
    being a day stale.
    """
    payload = parse(read_token(path), public_key_hex)
    if payload.get("state") in (MISSING, INVALID):
        return payload

    kid = payload.get("kid", "")
    issuer = issuer or payload.get("issuer", "")
    if allow_network and (force or _due_for_check(path, kid)):
        revoked = check_revoked(kid, issuer, path)
    else:
        revoked = bool(_cached_revocation(path).get("revoked"))
    return evaluate(payload, revoked=revoked)


def refresh(path, public_key_hex: str, issuer: str = "") -> dict:
    """Ask the publisher, then report. For the background refresher only."""
    return status(path, public_key_hex, issuer, allow_network=True, force=True)


CACHE_SECONDS = 60


def cached_status(path, public_key_hex: str, issuer: str = "") -> dict:
    """Licence state for the request path — memoised, and never touches the network.

    Verifying a signature is microseconds, but doing it plus two small file reads
    on every request to every endpoint is waste for an answer that changes at
    most once a day.
    """
    now = ist.now()
    seen, value = _runtime.get("checked_at"), _runtime.get("state")
    if value and seen and (now - seen).total_seconds() < CACHE_SECONDS:
        return value
    value = status(path, public_key_hex, issuer)
    _runtime.update({"checked_at": now, "state": value})
    return value


def forget() -> None:
    """Drop the memoised state — after installing a new licence."""
    _runtime.update({"checked_at": None, "state": None})


# ----------------------------------------------------------- announcements
def _seen_file(path):
    """Which announcements this copy has already stored."""
    return path.with_name(path.name + ".news")


def _last_seen(path) -> int:
    try:
        return int(json.loads(_seen_file(path).read_text(encoding="utf-8")).get("last", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _remember_seen(path, last: int) -> None:
    try:
        _seen_file(path).write_text(json.dumps({"last": int(last)}), encoding="utf-8")
    except OSError:
        pass


def pull_announcements(db, path, public_key_hex: str, issuer: str = "",
                       timeout: float = 8.0) -> int:
    """Fetch anything the supplier has said, and store it as notifications.

    Runs on the same background thread as the revocation check, and for the same
    reason: a copy on somebody else's laptop cannot be pushed to, so it has to
    ask. Turning each message into an ordinary notification means it behaves like
    every other one — it waits in the bell if the phone was off, and survives a
    push that never arrived.
    """
    payload = parse(read_token(path), public_key_hex)
    if payload.get("state") in (MISSING, INVALID):
        return 0
    kid = payload.get("kid", "")
    issuer = (issuer or payload.get("issuer", "")).rstrip("/")
    if not (kid and issuer):
        return 0

    import requests
    last = _last_seen(path)
    try:
        r = requests.get(f"{issuer}/api/licence/announcements/{kid}",
                         params={"since": last}, timeout=timeout)
        if r.status_code != 200:
            return 0
        items = (r.json() or {}).get("items") or []
    except Exception:
        return 0                # unreachable supplier is not the customer's problem
    if not items:
        return 0

    from .models import Notification, User
    from . import ist as _ist
    stored = 0
    users = [uid for (uid,) in db.query(User.id).filter(User.status == "active").all()]
    for item in items:
        title = (item.get("title") or "")[:160]
        body = item.get("body") or ""
        if not title:
            continue
        for uid in users:
            db.add(Notification(user_id=uid, kind="system", title=title, body=body,
                                url=(item.get("url") or "/")[:255], is_read=0, pushed=0,
                                created_at=_ist.now()))
            stored += 1
    db.commit()
    _remember_seen(path, max(int(i.get("id", 0)) for i in items))
    return stored
