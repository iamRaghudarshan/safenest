"""Time-based one-time passwords (RFC 6238) — the second factor.

WHY THIS IS WRITTEN OUT RATHER THAN INSTALLED. `pyotp` would do it in one
import, and `requirements.txt` is installed on every customer's machine — so
each dependency added here is another package on someone else's computer,
another thing to keep patched, and another supply-chain link in a product whose
argument is that it is self-contained. TOTP is HMAC-SHA1 over a counter and a
modulo; it is short enough to read, and this file has no imports beyond the
standard library.

Interoperable with Google Authenticator, Authy, 1Password and Apple's built-in
verification codes — the algorithm is fixed by the RFC, and the only choices
are the ones stated below.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

# 30-second steps and 6 digits: the values every authenticator app assumes. They
# are technically parameters and are in practice not — an app given anything
# else shows codes that are simply wrong, with nothing to explain why.
STEP = 30
DIGITS = 6

# How many steps either side of now are accepted.
#
# 1 means a code stays valid for about 90 seconds. Phone clocks drift, and
# somebody typing a six-digit code as it rolls over should not be told they got
# it wrong. Wider than this starts giving a stolen code a useful lifetime.
WINDOW = 1


def new_secret() -> str:
    """A fresh base32 secret. 20 bytes — the RFC's recommended length for
    HMAC-SHA1, and what every authenticator app expects to be handed."""
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    # Padding restored: base32 decoding is strict about it and the secret is
    # stored stripped, because that is how these are shown to people.
    pad = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret.upper() + pad, casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, *, at: float | None = None) -> bool:
    """True if `code` is valid right now.

    Compared with compare_digest, not ==. A plain comparison returns as soon as
    two characters differ, and the time it takes leaks how much of the code was
    right — which over enough attempts is worth more than it sounds for a
    six-digit number.
    """
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return False
    now = int((at if at is not None else time.time()) // STEP)
    for drift in range(-WINDOW, WINDOW + 1):
        if hmac.compare_digest(_code_at(secret, now + drift), code):
            return True
    return False


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """The otpauth:// URI an authenticator app reads from a QR code.

    The issuer appears BOTH as a path prefix and as a parameter. That is not
    redundancy for its own sake: older apps read one and newer ones read the
    other, and an app that finds neither lists the entry as "Unknown", which is
    useless to somebody with four accounts in it.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


# --------------------------------------------------------- recovery codes ---
#
# NOT OPTIONAL, and the reason is specific to this product. A customer runs
# their own copy with no administrator anywhere in it — by design, see §10 of
# the project guide. If they turn on two-factor and then lose or wipe their
# phone, there is nobody to ring. Without these codes the second factor would
# not be a security feature; it would be a way to lose every financial record
# you own, permanently, by replacing a handset.

RECOVERY_COUNT = 10


def new_recovery_codes() -> list[str]:
    """Ten single-use codes, in a shape people can copy off a screen onto paper
    without mistaking O for 0."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I, O, 0, 1
    out = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def hash_recovery(code: str) -> str:
    """Stored hashed, never in the clear — they are passwords that happen to be
    used once. SHA-256 rather than bcrypt here because these are 50 bits of
    real randomness, not something a person chose, so there is nothing for a
    slow hash to defend against."""
    return hashlib.sha256(normalise_recovery(code).encode()).hexdigest()


def normalise_recovery(code: str) -> str:
    """Accept what somebody actually types: spaces, lower case, missing dash."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())
