"""Where this installation can be reached from the internet.

Historically this was `PUBLIC_BASE_URL` in .env — fixed at install time, changed
only by editing a file and restarting. That is wrong for something an owner may
well change: they buy a domain, or move to a different one, and should be able to
say so from inside the app.

Everything now reads `public_url(db)`. It answers from the database when a value
has been set there, and falls back to the .env value otherwise, so an installation
that never opens the new screen behaves exactly as it always did.

Not to be confused with `hosts.py`, which records which *computers* the app has
run on. This module is about the address, not the machine.
"""
import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import ist
from .config import settings
from .models import Hosting

# A hostname label per RFC 1123, joined by dots, with a TLD of at least two
# letters. Deliberately strict: this string ends up inside signed licence tokens
# and in a tunnel config, so "looks about right" is not good enough.
_HOST = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _row(db: Session) -> Hosting:
    """The single hosting row, created on first use.

    Tolerates losing the insert race for the same reason branding does: several
    requests can arrive together on a fresh install.
    """
    row = db.query(Hosting).filter(Hosting.id == 1).first()
    if row:
        return row
    try:
        row = Hosting(id=1, public_url="", tunnel_hostname="", tunnel_id="",
                      tunnel_token="", updated_at=ist.now())
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        row = db.query(Hosting).filter(Hosting.id == 1).first()
        if row:
            return row
        raise


def public_url(db: Session) -> str:
    """The address customers' copies should call, with no trailing slash.

    Empty string means this installation is not published — the app still works
    on the local network, licences simply have nothing to check in against.
    """
    try:
        stored = (_row(db).public_url or "").strip()
    except Exception:
        stored = ""
    return (stored or settings.public_base_url or "").rstrip("/")


def normalise(value: str) -> str:
    """Accept what a person would actually type and return a canonical URL.

    People paste "finmate.example.com", "https://finmate.example.com/",
    "HTTPS://the app.Example.com" and all of them mean the same thing. Raises
    ValueError with a message worth showing when it cannot be made sense of.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"^\s*https?://", "", raw, flags=re.I).strip().strip("/")
    raw = raw.split("/")[0].split("?")[0].lower()
    if not raw:
        raise ValueError("That does not look like a web address.")
    if ":" in raw:
        raise ValueError("Leave the port out — the tunnel handles that.")
    if not _HOST.match(raw):
        raise ValueError(
            "That does not look like a domain name. Use something like "
            "finmate.yourdomain.com")
    # Always https: the tunnel terminates TLS, and a plain-http address would
    # send the licence key and every request in the clear.
    return f"https://{raw}"


def hostname_of(url: str) -> str:
    """Just the host part, for the tunnel config."""
    return (url or "").split("//")[-1].split("/")[0]
