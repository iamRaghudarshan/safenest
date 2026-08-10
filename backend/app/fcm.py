"""Push to the phone app, through Firebase Cloud Messaging.

WHY THIS EXISTS AT ALL, given §1. Web push (VAPID) reaches a browser and the
installed PWA; it cannot reach a native app, because Apple and Google will only
deliver to a native app through APNs and FCM respectively. There is no
self-hosted route to an iPhone's lock screen — that is Apple's decision, not a
gap in this design.

So this is the one place where something leaves the machine, and it is worth
being exact about WHAT. A notification carries a title and a line of body text —
"HDFC card due today". It does not carry the record, the amount, the vault, or
anything a person would call their data. If even that is too much, the answer is
to leave FCM unconfigured and use the phone's own alarms (`alarms.dart`), which
are scheduled on the device and reach nobody. Both work; they are different
trades and the owner picks.

NO NEW DEPENDENCY. The obvious way is `google-auth`, which pulls in a
dependency tree that would then be installed on every customer's machine. FCM's
HTTP v1 API wants an OAuth2 access token, obtained by presenting a JWT signed
with the service account's RSA key — and `cryptography` is already here for the
vault. So the token is minted directly, in about thirty lines.

UNCONFIGURED IS A NO-OP, NEVER AN ERROR. Most installations will never set this
up, and a reminder must not fail to be recorded because a push could not be
sent.
"""
from __future__ import annotations

import base64
import json
import os
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .config import settings

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# The access token lasts an hour; minting one per notification would add a
# round trip to Google in front of every reminder.
_cached: dict = {"token": None, "expires": 0.0}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _service_account() -> dict | None:
    """The JSON Firebase gives you for a service account, or None.

    Read from disk each time rather than held in memory: it is a credential,
    and an installation that revokes it should stop working at once rather than
    until the next restart.
    """
    path = (settings.fcm_service_account or "").strip()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("private_key") and data.get("client_email"):
            return data
    except Exception as exc:
        print(f"[fcm] service account file unreadable: {exc}")
    return None


def configured() -> bool:
    return _service_account() is not None


def _access_token() -> str | None:
    now = time.time()
    if _cached["token"] and _cached["expires"] - 60 > now:
        return _cached["token"]

    sa = _service_account()
    if not sa:
        return None

    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": sa["client_email"],
        "scope": _SCOPE,
        "aud": _TOKEN_URL,
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    try:
        key = serialization.load_pem_private_key(
            sa["private_key"].encode(), password=None)
        signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        assertion = f"{signing_input}.{_b64(signature)}"
        r = requests.post(_TOKEN_URL, timeout=15, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        })
        if r.status_code != 200:
            print(f"[fcm] token exchange refused ({r.status_code}): {r.text[:160]}")
            return None
        body = r.json()
        _cached["token"] = body["access_token"]
        _cached["expires"] = now + int(body.get("expires_in", 3600))
        return _cached["token"]
    except Exception as exc:
        print(f"[fcm] could not mint an access token: {exc}")
        return None


def send(token: str, title: str, body: str, data: dict | None = None) -> tuple[bool, str]:
    """One notification to one device. (delivered, reason).

    The reason matters to the caller: a token Google reports as UNREGISTERED is
    a device that has uninstalled the app or reinstalled it, and its row should
    be deleted rather than retried for ever. Anything else is worth keeping.
    """
    sa = _service_account()
    access = _access_token()
    if not sa or not access:
        return False, "not-configured"

    url = (f"https://fcm.googleapis.com/v1/projects/"
           f"{sa.get('project_id')}/messages:send")
    message = {
        "message": {
            "token": token,
            # `notification` so the phone shows it while the app is closed —
            # a data-only message is delivered to a running app and silently
            # dropped by iOS otherwise, which is the whole case this is for.
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in (data or {}).items()},
            "android": {
                "priority": "high",
                "notification": {"channel_id": "safenest.reminders.alarm"},
            },
            "apns": {
                "headers": {"apns-priority": "10"},
                "payload": {"aps": {"sound": "default",
                                    "interruption-level": "time-sensitive"}},
            },
        }
    }
    try:
        r = requests.post(url, timeout=20,
                          headers={"Authorization": f"Bearer {access}",
                                   "Content-Type": "application/json"},
                          json=message)
        if r.status_code == 200:
            return True, "ok"
        # 404 UNREGISTERED / 400 INVALID_ARGUMENT on the token both mean the
        # registration is dead. Named so the caller can prune it.
        text = r.text[:200]
        if r.status_code in (400, 403, 404) and "UNREGISTERED" in text.upper():
            return False, "unregistered"
        if r.status_code == 404:
            return False, "unregistered"
        print(f"[fcm] send failed ({r.status_code}): {text}")
        return False, f"http-{r.status_code}"
    except Exception as exc:
        print(f"[fcm] send failed: {exc}")
        return False, "error"
