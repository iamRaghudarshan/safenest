"""The web address this copy answers on, changeable from inside the app.

Two audiences use this screen and both are administrators of their own copy:

  * the publisher, changing where their own installation lives, and
  * someone who bought a licensed copy, bought a domain, and wants to reach their
    records from outside the house.

Both need the same three things: set the address, put the tunnel credentials in,
and be told plainly whether it actually works.

WHAT THIS DELIBERATELY DOES NOT DO
It does not restart the tunnel. Writing a config file is safe and reversible;
stopping and starting a Windows service from inside a web request is neither, and
would cut off the very connection the request arrived on. The screen writes the
config and tells the person the one command to run.
"""
import ipaddress
import os
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import autostart, ist, tunnelrun, weburl
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import Hosting, License, User
from ..ratelimit import rate_limit
from ..security import get_current_user

router = APIRouter(prefix="/api/hosting", tags=["hosting"])


def can_manage(user: User) -> bool:
    """Who is allowed to change the address this copy answers on.

    An administrator always. And in a LICENSED copy, any signed-in user: that copy
    belongs to the person running it, they bought the domain, and they are given
    the `user` role deliberately so they have no administrative powers over the
    publisher's product. Withholding this from them would put the setting behind a
    role that, by design, nobody in a customer's copy ever has - the feature would
    exist and be unreachable for exactly the people it is for.

    On the publisher's own installation, licensed_mode is off and several people
    may share it, so it stays admin-only.
    """
    return user.role == "admin" or settings.licensed_mode


def _manager(user: User = Depends(get_current_user)) -> User:
    if not can_manage(user):
        raise HTTPException(
            403, "Only an administrator can change the web address on this copy.")
    return user

# Where cloudflared looks for its configuration when it runs as a service.
CF_HOME = Path(os.path.expanduser("~")) / ".cloudflared"


def _mask(token: str) -> str:
    """Enough to recognise a token, never enough to use one."""
    t = (token or "").strip()
    if not t:
        return ""
    return f"{t[:6]}…{t[-4:]} ({len(t)} characters)" if len(t) > 14 else "…"


def _state(db: Session) -> dict:
    row = weburl._row(db)
    url = weburl.public_url(db)
    from_env = not (row.public_url or "").strip()
    cfg = CF_HOME / "config.yml"
    return {
        "public_url": url,
        "hostname": weburl.hostname_of(url),
        # True when the address is still the one compiled into .env rather than
        # one chosen here — worth showing, because it explains "why is this
        # filled in when I never set it".
        "from_env": from_env and bool(url),
        "env_url": (settings.public_base_url or "").rstrip("/"),
        "tunnel_hostname": row.tunnel_hostname or "",
        "tunnel_id": row.tunnel_id or "",
        "has_token": bool((row.tunnel_token or "").strip()),
        "token_hint": _mask(row.tunnel_token or ""),
        "config_path": str(cfg),
        "config_written": cfg.is_file(),
        "updated_at": ist.fmt(row.updated_at) if row.updated_at else None,
    }


@router.get("")
def read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Current address and tunnel state.

    Anyone who may change it sees everything; anyone else sees only the address
    itself, which is not a secret - it is what they type into a browser.
    """
    s = _state(db)
    if not can_manage(user):
        # Still useful to a plain user on a shared installation - it is the
        # address they would type on their phone - but nothing operational.
        return {"public_url": s["public_url"], "hostname": s["hostname"],
                "can_manage": False}
    return {**s, "can_manage": True}


@router.put("")
def update(request: Request, body: dict = Body(...),
           admin: User = Depends(_manager), db: Session = Depends(get_db)):
    """Set the address this copy is reachable at.

    Changing it does NOT move licences that are already out there. Their issuer is
    inside a signed token and cannot be rewritten from here, so the old address has
    to keep working or those copies lose contact. The response says so explicitly
    and the screen repeats it — this is the kind of thing that is discovered
    months later otherwise.
    """
    raw = body.get("public_url", "")
    try:
        url = weburl.normalise(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    row = weburl._row(db)
    previous = weburl.public_url(db)
    row.public_url = url
    row.updated_at = ist.now()
    row.updated_by = admin.id
    db.commit()

    # How many copies are still pointed at the old address?
    stale = 0
    if previous and url and previous != url:
        stale = db.query(License).filter(License.revoked_at.is_(None)).count()

    audit(db, admin.id, "hosting_update", "hosting", 1,
          {"label": url or "not published", "was": previous}, request=request)
    return {**_state(db), "previous_url": previous, "licences_on_old_url": stale}


@router.put("/tunnel")
def set_tunnel(request: Request, body: dict = Body(...),
               admin: User = Depends(_manager), db: Session = Depends(get_db)):
    """Store the Cloudflare tunnel id and token for this address."""
    row = weburl._row(db)
    tunnel_id = (body.get("tunnel_id") or "").strip()[:64]
    token = (body.get("tunnel_token") or "").strip()
    hostname = (body.get("hostname") or "").strip().lower()[:255]

    if hostname:
        try:
            hostname = weburl.hostname_of(weburl.normalise(hostname))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    row.tunnel_hostname = hostname or weburl.hostname_of(weburl.public_url(db))
    if tunnel_id:
        row.tunnel_id = tunnel_id
    # An empty token means "leave the one already stored alone" — the screen never
    # shows the full token back, so it cannot round-trip it.
    if token:
        row.tunnel_token = token
    row.updated_at = ist.now()
    row.updated_by = admin.id
    db.commit()
    audit(db, admin.id, "hosting_tunnel", "hosting", 1,
          {"label": row.tunnel_hostname}, request=request)
    return _state(db)


@router.post("/config")
def write_config(request: Request, admin: User = Depends(_manager),
                 db: Session = Depends(get_db)):
    """Write ~/.cloudflared/config.yml from what is stored here.

    Deliberately does not restart anything — see the module note. Returns the
    exact command to run, so the person is not left guessing.
    """
    row = weburl._row(db)
    host = row.tunnel_hostname or weburl.hostname_of(weburl.public_url(db))
    if not host:
        raise HTTPException(422, "Set the web address first.")
    if not row.tunnel_id:
        raise HTTPException(
            422, "Set the tunnel id first — cloudflared prints it when you run "
                 "'cloudflared tunnel create'.")

    creds = CF_HOME / f"{row.tunnel_id}.json"
    CF_HOME.mkdir(parents=True, exist_ok=True)
    (CF_HOME / "config.yml").write_text(
        f"# Written by {settings.public_base_url and 'the app' or 'the app'} — "
        f"edit it here or in the Web address screen.\n"
        f"tunnel: {row.tunnel_id}\n"
        f"credentials-file: {creds}\n"
        f"\n"
        f"ingress:\n"
        f"  - hostname: {host}\n"
        f"    service: http://127.0.0.1:8080\n"
        f"  - service: http_status:404\n",
        encoding="utf-8")

    audit(db, admin.id, "hosting_config", "hosting", 1, {"label": host},
          request=request)
    return {
        **_state(db),
        "written_to": str(CF_HOME / "config.yml"),
        "credentials_present": creds.is_file(),
        "next_command": "net stop cloudflared && net start cloudflared",
        "note": ("The credentials file is missing. Run 'cloudflared tunnel login' "
                 "then 'cloudflared tunnel create <name>' on this computer."
                 if not creds.is_file() else ""),
    }


@router.post("/check")
def check(admin: User = Depends(_manager), db: Session = Depends(get_db)):
    """Ask the public address whether it reaches *this* server.

    Comparing a build marker rather than just looking for a 200 matters: a domain
    that resolves to somebody else's site, or to an older copy of this app on
    another machine, answers 200 perfectly happily. That is the failure worth
    catching, because everything looks fine from the outside.
    """
    url = weburl.public_url(db)
    if not url:
        return {"ok": False, "reason": "No web address is set."}
    import requests
    try:
        r = requests.get(f"{url}/api/health", timeout=12)
    except requests.exceptions.SSLError:
        return {"ok": False, "url": url,
                "reason": "The address answered but its certificate was rejected."}
    except Exception:
        return {"ok": False, "url": url,
                "reason": "Nothing answered. The tunnel may be stopped, or DNS has "
                          "not finished pointing at it yet."}
    if r.status_code != 200:
        return {"ok": False, "url": url,
                "reason": f"The address answered with {r.status_code}, not the app."}
    try:
        body = r.json()
    except Exception:
        return {"ok": False, "url": url,
                "reason": "Something answered, but it is not this app."}
    if body.get("service") != "finmate-api":
        return {"ok": False, "url": url,
                "reason": "That address belongs to a different site."}
    return {"ok": True, "url": url, "reason": "Reachable, and it is this server."}


# ----------------------------------------------------- set the domain up for them
@router.post("/auto")
def auto_setup(request: Request, body: dict = Body(...),
               user: User = Depends(_manager), db: Session = Depends(get_db)):
    """Domain in, working address out — steps 4 to 7 of the walkthrough, done here.

    The two steps before this still need a person: only the owner can add a domain
    to their Cloudflare account and change the nameservers at their registrar.
    Everything after is Cloudflare's API, so there is no reason to make somebody
    type it into a terminal.

    The API token is used and dropped. It can edit DNS and create tunnels across
    the whole zone, which is far more than this app should hold on to; the tunnel
    token it produces is scoped to one tunnel and is the only thing kept.
    """
    from .. import cfsetup, tunnelrun

    raw = (body.get("public_url") or body.get("domain") or "").strip()
    token = (body.get("cf_api_token") or "").strip()
    if not token:
        raise HTTPException(422, "Paste your Cloudflare API token.\n\n"
                                 + cfsetup.TOKEN_HELP)
    if not cfsetup.looks_like_token(token):
        raise HTTPException(422, "That does not look like an API token. Copy the "
                                 "whole value Cloudflare showed you — not the "
                                 "token *id*, which is the short one beside it.")
    try:
        url = weburl.normalise(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    host = weburl.hostname_of(url)

    try:
        result = cfsetup.setup(token, host, port=_request_port(request),
                               tunnel_name=(body.get("tunnel_name") or "").strip())
    except cfsetup.SetupError as exc:
        raise HTTPException(422, str(exc))

    row = weburl._row(db)
    previous = weburl.public_url(db)
    row.public_url = url
    row.tunnel_hostname = host
    row.tunnel_id = result["tunnel_id"]
    row.tunnel_token = result["tunnel_token"]
    row.updated_at = ist.now()
    row.updated_by = user.id
    db.commit()

    # Restarting our own supervised connector is safe -- it is a child process of
    # this app, not the Windows service, and it is not carrying this request.
    try:
        tunnelrun.restart()
    except Exception:
        pass

    stale = 0
    if previous and previous != url:
        stale = db.query(License).filter(License.revoked_at.is_(None)).count()
    audit(db, user.id, "hosting_auto", "hosting", 1, {"label": host}, request=request)
    return {**_state(db), "created": not result["reused"],
            "tunnel_name": result["tunnel_name"], "zone": result["zone"],
            "connector": tunnelrun.status(), "licences_on_old_url": stale}


@router.post("/auto/check")
def auto_check(body: dict = Body(...), user: User = Depends(_manager)):
    """Confirm the token and the domain fit together, before changing anything.

    Separate from /auto so the two failures people actually hit -- a token for the
    wrong account, and nameservers that have not switched over yet -- are reported
    while nothing has been created.
    """
    from .. import cfsetup
    token = (body.get("cf_api_token") or "").strip()
    raw = (body.get("public_url") or body.get("domain") or "").strip()
    try:
        host = weburl.hostname_of(weburl.normalise(raw))
        zone = cfsetup.find_zone(token, host)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except cfsetup.SetupError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "zone": zone.get("name"), "hostname": host,
            "status": zone.get("status")}


# ------------------------------------------------------ reaching it on the Wi-Fi
def _request_port(request: Request) -> int:
    """The port this very request arrived on.

    Read from the live connection rather than from configuration: the launcher
    moves to another port when 8080 is busy, so anything derived from a setting
    can name a port nothing is listening on.
    """
    try:
        return int(request.url.port or (443 if request.url.scheme == "https" else 80))
    except Exception:
        return 8080


def _reached_locally(host: str) -> bool:
    """True when this request arrived on a LAN or loopback address, not the domain.

    Decides whether it is safe to hand back this machine's private IP. A client
    already talking to us over the LAN learns nothing it did not already have; the
    public internet, reaching us through the tunnel with the domain in its Host
    header, has no business being told the internal address and could not use it
    anyway. So the switcher only ever offers "reach this from anywhere" to someone
    at home — never "switch to 192.168.x" to a stranger on the far side of the
    tunnel.
    """
    h = (host or "").split(":")[0].strip().lower()
    if not h or h == "localhost":
        return bool(h)          # "" is unknown; "localhost" is local
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False            # a hostname/domain means we were reached over the internet


@router.get("/addresses")
def addresses(request: Request, db: Session = Depends(get_db)):
    """The addresses this copy answers on, for the sign-in screen's connection
    switch. Public on purpose — it sits before anyone has a token, exactly like
    /api/branding, and returns only addresses, nothing about users or data.

    `lan` is populated only for a client that reached us locally (see
    _reached_locally); `public` is the address the owner published, which is
    already public by definition. Either may be "" — the switcher simply shows
    nothing it cannot offer.
    """
    from .. import lanaccess
    rate_limit(request, "addresses", limit=120, window=60)
    local = _reached_locally(request.headers.get("host", ""))
    return {
        "current": "lan" if local else "internet",
        "lan": lanaccess.lan_url(_request_port(request)) if local else "",
        "public": weburl.public_url(db),
    }


@router.get("/local-network")
def local_network(request: Request, user: User = Depends(_manager)):
    """Can a phone on the same Wi-Fi reach this computer?

    Its own section because it fails in a way nothing else reports: the launcher
    prints a phone address, uvicorn really is listening on it, and Windows
    Firewall drops the connection in between. From the phone that is an
    indistinguishable timeout, so the app has to be the thing that says so.
    """
    from .. import lanaccess
    rule = lanaccess.exists()
    private = lanaccess.on_private_network()
    return {"platform": "windows" if os.name == "nt" else "other",
            # Reported separately because they fail the same way and are fixed
            # differently: a missing rule is ours to add, a Public network is a
            # Windows setting only the person there should change.
            "rule": rule,
            "private_network": private,
            "networks": lanaccess.networks(),
            "allowed": rule and private,
            "address": lanaccess.lan_url(_request_port(request)),
            "advice": "" if (rule and private) else lanaccess.advice(0)}


@router.post("/local-network/private")
def set_network_private(request: Request, user: User = Depends(_manager),
                        db: Session = Depends(get_db)):
    """Mark the connected Wi-Fi as Private so the firewall rule applies at all."""
    from .. import lanaccess
    ok, how = lanaccess.make_private(elevate=True)
    if not ok:
        raise HTTPException(422, f"Windows would not change it ({how}). On the "
                                 f"Wi-Fi settings screen, set this network to "
                                 f"Private and try again.")
    audit(db, user.id, "lan_private", "hosting", 1, {"label": how}, request=request)
    return {"private_network": lanaccess.on_private_network(),
            "networks": lanaccess.networks(), "how": how}


@router.post("/local-network")
def allow_local_network(request: Request, user: User = Depends(_manager),
                        db: Session = Depends(get_db)):
    """Add the firewall rule, asking for permission if this copy lacks it.

    Elevation is requested here and not at startup: a UAC dialog nobody asked for,
    appearing at login on a machine somebody was handed, reads as malware. Asked
    for by someone who has just pressed "Allow on my Wi-Fi", it reads as the
    thing they pressed.
    """
    from .. import lanaccess
    ok, how = lanaccess.ensure(elevate=True)
    if not ok:
        raise HTTPException(422, f"Windows would not allow it ({how}). "
                                 f"{lanaccess.advice(0)}")
    audit(db, user.id, "lan_allow", "hosting", 1, {"label": how}, request=request)
    return {"allowed": True, "how": how,
            "address": lanaccess.lan_url(_request_port(request))}


@router.delete("/local-network")
def block_local_network(request: Request, user: User = Depends(_manager),
                        db: Session = Depends(get_db)):
    from .. import lanaccess
    ok = lanaccess.remove()
    audit(db, user.id, "lan_block", "hosting", 1, {"label": "removed"}, request=request)
    return {"allowed": not ok, "address": lanaccess.lan_url(_request_port(request))}


# --------------------------------------------------------- always-on behaviour
@router.get("/always-on")
def always_on(user: User = Depends(_manager)):
    """Is this computer set up to serve the owner's records whenever it is on?

    Two separate things have to be true, and saying which is which matters: the
    app has to start at login, and the connector has to be running. Reporting one
    combined "on/off" would hide a half-set-up state, which is exactly the state
    that produces "my address stopped working and I don't know why".
    """
    return {"startup": autostart.status(), "tunnel": tunnelrun.status()}


@router.post("/always-on")
def enable_always_on(request: Request, admin: User = Depends(_manager),
                     db: Session = Depends(get_db)):
    """Start with the computer. No administrator rights needed — see autostart.py."""
    try:
        st = autostart.enable()
    except RuntimeError as exc:
        raise HTTPException(422, str(exc))
    audit(db, admin.id, "autostart_on", "hosting", 1, {"label": st.get("path", "")},
          request=request)
    return {"startup": st, "tunnel": tunnelrun.status()}


@router.delete("/always-on")
def disable_always_on(request: Request, admin: User = Depends(_manager),
                      db: Session = Depends(get_db)):
    try:
        st = autostart.disable()
    except RuntimeError as exc:
        raise HTTPException(422, str(exc))
    audit(db, admin.id, "autostart_off", "hosting", 1, {"label": "disabled"},
          request=request)
    return {"startup": st, "tunnel": tunnelrun.status()}
