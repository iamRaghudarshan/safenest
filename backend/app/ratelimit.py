"""Lightweight in-memory, per-IP sliding-window rate limiter for sensitive
endpoints (login, vault reveal). Single-instance app, so an in-process store is
sufficient."""
import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status

_buckets: dict[str, deque] = {}
_lock = Lock()

# Forwarded-IP headers are only believed when the connection itself came from the
# loopback interface — i.e. from cloudflared / tailscale / a local reverse proxy.
# A client reaching the app directly (LAN HTTPS on 0.0.0.0:8443) can set any header
# it likes, so trusting these unconditionally would let anyone mint a fresh rate
# limit bucket per request and brute-force the login endpoint freely.
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def client_ip(request: Request) -> str:
    """Real visitor IP, resolved safely regardless of how the app is exposed."""
    peer = request.client.host if request.client else ""
    if peer in _LOOPBACK:
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return peer or "unknown"


def rate_limit(request: Request, bucket: str, limit: int, window: int) -> None:
    """Allow at most `limit` hits per `window` seconds per IP for this `bucket`.
    Raises 429 with Retry-After when exceeded."""
    now = time.time()
    key = f"{bucket}:{client_ip(request)}"
    with _lock:
        dq = _buckets.setdefault(key, deque())
        while dq and dq[0] <= now - window:
            dq.popleft()
        if len(dq) >= limit:
            retry = int(window - (now - dq[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts. Please wait a bit and try again.",
                headers={"Retry-After": str(retry)},
            )
        dq.append(now)
        # opportunistic cleanup so the dict can't grow unbounded
        if len(_buckets) > 5000:
            for k in [k for k, v in _buckets.items() if not v or v[-1] <= now - window]:
                _buckets.pop(k, None)
