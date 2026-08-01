"""Short-lived signed URLs for private media.

Photo grids render hundreds of <img> tags, which cannot carry an Authorization
header. Instead the (authenticated, ownership-checked) list endpoints hand out
URLs carrying an HMAC that binds the file to one owner and an expiry — the same
shape as an S3 pre-signed URL. Nothing is guessable and nothing is permanent.
"""
import hashlib
import hmac
import time

from .config import settings

_KEY = settings.media_secret.encode()


def _mac(owner_id: int, name: str, exp: int) -> str:
    msg = f"{owner_id}:{name}:{exp}".encode()
    return hmac.new(_KEY, msg, hashlib.sha256).hexdigest()[:32]


def sign(owner_id: int, name: str, ttl: int | None = None) -> str:
    """Return the `exp.mac` query token for one file belonging to one user."""
    exp = int(time.time()) + (ttl or settings.media_url_ttl)
    return f"{exp}.{_mac(owner_id, name, exp)}"


def verify(owner_id: int, name: str, token: str) -> bool:
    """Constant-time check that `token` was issued for this owner+file and is unexpired."""
    try:
        exp_s, mac = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(mac, _mac(owner_id, name, exp))
