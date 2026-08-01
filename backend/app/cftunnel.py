"""Give each licensed copy its own web address, without the customer touching Cloudflare.

When a licence is issued this creates a tunnel, points a subdomain at it, and hands
the tunnel's token to the bundler. The customer's copy starts cloudflared with that
token and answers on their own address. They never see a dashboard, never create an
account, and never paste a token — which is exactly the wall people hit otherwise.

Tunnels are created as REMOTELY MANAGED (`config_src: cloudflare`), so their routing
lives in Cloudflare rather than in a config file on the customer's machine. That is
what lets the address be repointed, or switched off, from here.

WITHDRAWING ACCESS
Deleting the DNS record cuts a copy off within seconds — the only truly immediate
control in this system. A revoked licence still has to wait for that machine to
phone home; a deleted hostname simply stops resolving.
"""
from __future__ import annotations

import re

import requests

from .config import settings

API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 20

# One label of a hostname: letters, digits and hyphens, not starting or ending with one.
LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


class CloudflareError(RuntimeError):
    """Something Cloudflare refused, phrased for whoever is looking at the screen."""


def _brand() -> str:
    """The app's current name, for the comment left on the DNS record.

    That comment is what the publisher sees years later in the Cloudflare
    dashboard, next to records they no longer remember creating -- so it should
    say what the product is called now, not what it was called at build time.
    """
    from .routers.branding import app_name
    return app_name()


def configured() -> bool:
    return bool(settings.cf_api_token and settings.cf_account_id and settings.cf_zone_id)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.cf_api_token}",
            "Content-Type": "application/json"}


def _call(method: str, path: str, **kw) -> dict:
    if not configured():
        raise CloudflareError(
            "Cloudflare is not set up on this server. Add CF_API_TOKEN, "
            "CF_ACCOUNT_ID and CF_ZONE_ID to backend/.env.")
    try:
        r = requests.request(method, f"{API}{path}", headers=_headers(),
                             timeout=TIMEOUT, **kw)
        body = r.json()
    except requests.RequestException as exc:
        raise CloudflareError(f"Could not reach Cloudflare: {exc}") from exc
    except ValueError as exc:
        raise CloudflareError("Cloudflare returned something unreadable.") from exc
    if not body.get("success"):
        errors = body.get("errors") or []
        detail = "; ".join(str(e.get("message") or e) for e in errors) or "unknown error"
        raise CloudflareError(detail)
    return body.get("result") or {}


# ------------------------------------------------------------------- naming
def suggest_label(name: str, email: str) -> str:
    """A subdomain label from whatever we know about them."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "-", (email or "").split("@")[0].lower()).strip("-")
    return (base or "customer")[:40].strip("-")


def valid_label(label: str) -> bool:
    return bool(LABEL.match((label or "").strip().lower()))


def hostname_for(label: str) -> str:
    return f"{label.strip().lower()}.{settings.licence_domain}"


# ------------------------------------------------------------------- tunnels
def create_tunnel(name: str, port: int = 8080, hostname: str = "") -> dict:
    """Make a tunnel, tell Cloudflare where it should route, and return its token."""
    tunnel = _call("POST", f"/accounts/{settings.cf_account_id}/cfd_tunnel",
                   json={"name": name, "config_src": "cloudflare"})
    tunnel_id = tunnel["id"]

    if hostname:
        # Ingress lives server-side for a remotely-managed tunnel. The trailing
        # catch-all is required: without it Cloudflare rejects the configuration.
        _call("PUT",
              f"/accounts/{settings.cf_account_id}/cfd_tunnel/{tunnel_id}/configurations",
              json={"config": {"ingress": [
                  {"hostname": hostname, "service": f"http://localhost:{int(port)}"},
                  {"service": "http_status:404"},
              ]}})

    token = _call("GET",
                  f"/accounts/{settings.cf_account_id}/cfd_tunnel/{tunnel_id}/token")
    return {"tunnel_id": tunnel_id, "token": token if isinstance(token, str) else "",
            "name": name}


def delete_tunnel(tunnel_id: str) -> None:
    try:
        _call("DELETE",
              f"/accounts/{settings.cf_account_id}/cfd_tunnel/{tunnel_id}?cascade=true")
    except CloudflareError:
        # Already gone, or deleted by hand in the dashboard. Withdrawing access
        # must not fail because the thing being withdrawn is missing.
        pass


# ----------------------------------------------------------------------- DNS
def route_dns(hostname: str, tunnel_id: str) -> str:
    """Point a hostname at a tunnel. Returns the DNS record id."""
    content = f"{tunnel_id}.cfargotunnel.com"
    existing = find_dns(hostname)
    if existing:
        _call("PATCH", f"/zones/{settings.cf_zone_id}/dns_records/{existing}",
              json={"type": "CNAME", "name": hostname, "content": content,
                    "proxied": True})
        return existing
    record = _call("POST", f"/zones/{settings.cf_zone_id}/dns_records",
                   json={"type": "CNAME", "name": hostname, "content": content,
                         "proxied": True,
                         "comment": f"{_brand()} licensed copy"})
    return record.get("id", "")


def find_dns(hostname: str) -> str:
    try:
        rows = _call("GET", f"/zones/{settings.cf_zone_id}/dns_records?name={hostname}")
    except CloudflareError:
        return ""
    if isinstance(rows, list) and rows:
        return rows[0].get("id", "")
    return ""


def delete_dns(hostname: str) -> bool:
    """Stop a hostname resolving. This is the immediate part of withdrawal."""
    record = find_dns(hostname)
    if not record:
        return False
    try:
        _call("DELETE", f"/zones/{settings.cf_zone_id}/dns_records/{record}")
        return True
    except CloudflareError:
        return False


# ------------------------------------------------------------------ combined
def provision(label: str, port: int = 8080) -> dict:
    """Everything a customer's copy needs to answer on its own address.

    Rolls back on a half-failure. A tunnel with no DNS record is invisible waste
    that still counts against the account, and it would be created afresh on every
    retry until somebody noticed.
    """
    label = (label or "").strip().lower()
    if not valid_label(label):
        raise CloudflareError(
            "Use letters, numbers and hyphens only — no spaces or dots.")
    hostname = hostname_for(label)
    made = create_tunnel(f"finmate-{label}", port=port, hostname=hostname)
    try:
        route_dns(hostname, made["tunnel_id"])
    except CloudflareError:
        delete_tunnel(made["tunnel_id"])
        raise
    return {"hostname": hostname, "url": f"https://{hostname}", **made}


def deprovision(hostname: str, tunnel_id: str) -> dict:
    """Cut a copy off. DNS first — that is the part the customer notices."""
    dns_gone = delete_dns(hostname) if hostname else False
    if tunnel_id:
        delete_tunnel(tunnel_id)
    return {"dns_removed": dns_gone, "tunnel_removed": bool(tunnel_id)}
