"""Set a customer's own domain up for them, from one API token.

WHY THIS EXISTS
The Web address screen walked someone through nine steps: add the domain to
Cloudflare, move the nameservers, install the connector, `cloudflared tunnel
login`, `tunnel create`, copy the id back into a box, `tunnel route dns`, save,
restart. Every one of those is a place to mistype something, and steps 4 to 7 are
a terminal — which is exactly what this product exists to spare people.

Only the first two genuinely need a human: nobody but the owner can add a domain
to a Cloudflare account or change nameservers at the registrar, and no API we can
call does it for them. Everything after that is Cloudflare's own API, so it is
done here instead.

THE TRICK THAT REMOVES THE TERMINAL
A tunnel created with `config_src: "cloudflare"` keeps its ingress rules
server-side, and the connector then runs from a token alone:

    cloudflared tunnel run --token <TOKEN>

No `tunnel login`, no cert.pem, no credentials JSON, no config.yml. That is what
lets steps 4 to 7 collapse into one button.

THE TOKEN IS NOT KEPT
The Cloudflare API token is used for the two or three calls below and then
discarded -- never written to the database, never returned to the browser. It can
create and delete tunnels and edit DNS for the customer's whole zone, which is far
more authority than this app needs to hold indefinitely. The tunnel token it
produces is scoped to that one tunnel, and that is the only thing worth storing.
"""
import re

import requests

API = "https://api.cloudflare.com/client/v4"
TIMEOUT = 20


class SetupError(Exception):
    """Something a person can act on — the message is shown to them verbatim."""


def _call(token: str, method: str, path: str, **kw) -> dict:
    try:
        r = requests.request(method, f"{API}{path}", timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"}, **kw)
        body = r.json()
    except requests.RequestException as exc:
        raise SetupError(f"Could not reach Cloudflare: {exc}") from exc
    except ValueError:
        raise SetupError("Cloudflare returned something unreadable.")
    if not body.get("success"):
        errs = body.get("errors") or []
        msg = "; ".join(str(e.get("message", e)) for e in errs) or f"HTTP {r.status_code}"
        if r.status_code in (401, 403):
            raise SetupError(
                "Cloudflare refused that token. Check it was copied whole, and "
                "that it has Zone:DNS:Edit and Account:Cloudflare Tunnel:Edit.")
        raise SetupError(msg)
    return body.get("result")


def apex_of(hostname: str) -> str:
    """The registrable domain a hostname sits under.

    "safenest.example.com" -> "example.com". Deliberately naive about multi-part
    suffixes (.co.uk, .co.in): those are checked against the account's real zone
    list below rather than guessed at, so a wrong guess here costs nothing.
    """
    parts = [p for p in (hostname or "").strip().lower().strip(".").split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else ".".join(parts)


def verify(token: str) -> dict:
    """Is this token live? Returns Cloudflare's own answer."""
    if not (token or "").strip():
        raise SetupError("Paste the API token first.")
    return _call(token.strip(), "GET", "/user/tokens/verify") or {}


def find_zone(token: str, hostname: str) -> dict:
    """The zone this hostname belongs to, matched against what the token can see.

    Matched by suffix rather than by a computed apex so that .co.in and .co.uk
    domains work without special-casing, and so the error can list what the token
    actually reaches — which is the useful thing when someone has pasted a token
    for the wrong account.
    """
    host = (hostname or "").strip().lower().strip(".")
    if not host:
        raise SetupError("Enter your web address first.")
    zones = _call(token, "GET", "/zones?per_page=50") or []
    names = [z.get("name", "") for z in zones]
    best = None
    for z in zones:
        name = (z.get("name") or "").lower()
        if name and (host == name or host.endswith("." + name)):
            if best is None or len(name) > len(best.get("name", "")):
                best = z
    if not best:
        raise SetupError(
            f"This token cannot see {apex_of(host)}. "
            + (f"It reaches: {', '.join(names)}. " if names else "It reaches no zones. ")
            + "Add the domain to Cloudflare first, then make the token for it.")
    if best.get("status") != "active":
        raise SetupError(
            f"{best.get('name')} is in Cloudflare but not active yet "
            f"(status: {best.get('status')}). That means the nameservers at your "
            "registrar have not switched over. Finish step 2 and try again.")
    return best


def _tunnel_named(token: str, account_id: str, name: str) -> dict | None:
    tunnels = _call(token, "GET",
                    f"/accounts/{account_id}/cfd_tunnel?name={name}&is_deleted=false")
    return (tunnels or [None])[0] if tunnels else None


def setup(token: str, hostname: str, port: int = 8080,
          tunnel_name: str = "", progress=lambda step, pct: None) -> dict:
    """Create (or reuse) a tunnel and point the address at it. Returns what to store.

    Reusing a tunnel of the same name matters: someone who runs this twice --
    because the first attempt half-worked, or they changed the port -- should end
    up with one working tunnel, not a second one competing with the first for the
    same hostname.
    """
    token = (token or "").strip()
    host = (hostname or "").strip().lower().strip(".")
    name = (tunnel_name or host.split(".")[0] or "app").strip()

    progress("Checking your Cloudflare token", 10)
    verify(token)

    progress("Finding your domain", 25)
    zone = find_zone(token, host)
    account_id = ((zone.get("account") or {}).get("id") or "").strip()
    if not account_id:
        raise SetupError("Cloudflare did not say which account that domain is in.")

    progress("Creating the tunnel", 45)
    existing = _tunnel_named(token, account_id, name)
    if existing:
        tunnel_id = existing["id"]
    else:
        created = _call(token, "POST", f"/accounts/{account_id}/cfd_tunnel",
                        json={"name": name, "config_src": "cloudflare"})
        tunnel_id = created["id"]

    progress("Telling it where to send traffic", 60)
    # Ingress is stored at Cloudflare, which is what lets the connector run from
    # the token alone. The catch-all is required — Cloudflare rejects a config
    # without one.
    _call(token, "PUT",
          f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
          json={"config": {"ingress": [
              {"hostname": host, "service": f"http://localhost:{int(port)}"},
              {"service": "http_status:404"},
          ]}})

    progress("Pointing your address at it", 75)
    upsert_dns(token, zone["id"], host, f"{tunnel_id}.cfargotunnel.com")

    progress("Collecting the connector token", 90)
    tunnel_token = _call(token, "GET",
                         f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")
    if not isinstance(tunnel_token, str) or not tunnel_token:
        raise SetupError("Cloudflare did not return a connector token.")

    progress("Done", 100)
    return {"hostname": host, "tunnel_id": tunnel_id, "tunnel_token": tunnel_token,
            "tunnel_name": name, "zone": zone.get("name", ""), "reused": bool(existing)}


def upsert_dns(token: str, zone_id: str, hostname: str, target: str) -> str:
    """Point hostname at target, replacing whatever is there.

    Replaces rather than adds: a second CNAME for the same name is an error at
    Cloudflare, and someone re-running setup after a failed attempt would hit it
    every time.
    """
    from .routers.branding import app_name
    found = _call(token, "GET", f"/zones/{zone_id}/dns_records?name={hostname}") or []
    payload = {"type": "CNAME", "name": hostname, "content": target,
               "proxied": True, "comment": f"{app_name()} — set up from the app"}
    if found:
        rec = found[0]
        _call(token, "PUT", f"/zones/{zone_id}/dns_records/{rec['id']}", json=payload)
        return rec["id"]
    rec = _call(token, "POST", f"/zones/{zone_id}/dns_records", json=payload)
    return (rec or {}).get("id", "")


TOKEN_HELP = (
    "In Cloudflare: My Profile → API Tokens → Create Token → Create Custom Token.\n"
    "Give it these two permissions, then Continue and Create:\n"
    "  • Zone → DNS → Edit\n"
    "  • Account → Cloudflare Tunnel → Edit\n"
    "Copy the token once it is shown — Cloudflare will not show it again."
)


def looks_like_token(value: str) -> bool:
    """Catch the commonest paste mistake before spending a network round trip."""
    v = (value or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{30,}", v))
