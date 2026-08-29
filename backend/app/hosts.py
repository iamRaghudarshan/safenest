"""Which computer is the app running on, and which ones has it run on before.

The app is designed to move: a bundle gets carried to a Mac, a copy runs off an
external drive, a laptop is replaced. From a phone every one of those looks
identical, which makes "why did my data disappear?" impossible to answer — the
usual cause is two machines serving the same address from two databases.

Recording the host on every start turns that into something visible. One row per
machine, so the table is also the history of where the app has lived.
"""
import hashlib
import platform
import socket
import uuid

from sqlalchemy.orm import Session

from . import ist
from .config import settings
from . import weburl
from .models import AppHost

WINDOWS, MAC, LINUX = "windows", "mac", "linux"


def platform_key() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return MAC
    if system == "windows":
        return WINDOWS
    return LINUX


def os_name() -> str:
    """A name someone would recognise, not a kernel string.

    platform.platform() on a Mac reports the Darwin kernel version (24.x), which
    nobody can map to the macOS version they actually installed.
    """
    kind = platform_key()
    if kind == MAC:
        release = platform.mac_ver()[0]
        return f"macOS {release}" if release else "macOS"
    if kind == WINDOWS:
        return f"Windows {platform.release()} {platform.version()}".strip()
    return f"{platform.system()} {platform.release()}".strip()


def local_ip() -> str:
    """The address other devices on the Wi-Fi would use.

    Resolving the hostname tends to give 127.0.0.1. Opening a UDP socket towards
    a public address makes the OS pick the interface it would really route from,
    without sending anything.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def fingerprint() -> str:
    """A stable id for this machine.

    Built from the MAC address and the OS, not the hostname or the IP — both of
    those change on their own (DHCP, renaming a laptop) and would otherwise read
    as a move to a different computer. uuid.getnode() falls back to a random
    value when it can find no adapter, so the hostname is mixed in to keep the
    id stable in that case too.
    """
    raw = f"{uuid.getnode()}|{platform.system()}|{platform.machine()}|{socket.gethostname()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def describe(db: Session | None = None) -> dict:
    """Facts about this machine. `db` is optional only so a caller without a
    session still gets everything except the live public address."""
    return {
        "fingerprint": fingerprint(),
        "hostname": socket.gethostname(),
        "platform": platform_key(),
        "os_name": os_name(),
        "local_ip": local_ip(),
        "public_url": weburl.public_url(db) if db is not None
                      else (settings.public_base_url or "").rstrip("/"),
        "data_dir": str(settings.sqlite_path.parent if settings.is_sqlite
                        else f"{settings.db_engine}://{settings.db_host}/{settings.db_name}"),
    }


def record(db: Session, app_version: str = "2.0") -> AppHost:
    """Note that the app is running here. Called once at startup.

    An existing machine is updated in place — its address or its OS version may
    have changed since last time, and neither means the app has moved.
    """
    now = ist.now()
    info = describe(db)

    host = db.query(AppHost).filter(
        AppHost.fingerprint == info["fingerprint"]).first()

    # THE FINGERPRINT IS NOT AS STABLE AS IT LOOKS, and a false "you are running
    # on two computers" is alarming in a way an ordinary bug is not: it tells
    # somebody their records are split across machines when they are not.
    #
    # It is built from uuid.getnode(), which returns the MAC of *an* adapter.
    # A laptop that moves between Wi-Fi and Ethernet, or gains a VPN adapter,
    # can hand back a different one — so the same machine registers twice. Seen
    # on the publisher's own box: two rows, both `PTS-048`, differing only in
    # the local IP that had changed between them.
    #
    # So before creating a new row, look for the same machine by what actually
    # identifies an installation: its hostname, its platform, and the data
    # directory it serves. Matching on the data dir is the important part —
    # two copies on ONE machine really are two installations and must still be
    # reported, which is what the warning exists for.
    if host is None:
        host = db.query(AppHost).filter(
            AppHost.hostname == info["hostname"],
            AppHost.platform == info["platform"],
            AppHost.data_dir == info["data_dir"],
        ).first()
        if host is not None:
            # Same installation, new fingerprint. Adopt it rather than leaving
            # a stale row behind to be counted as another computer.
            host.fingerprint = info["fingerprint"]

    if host is None:
        host = AppHost(fingerprint=info["fingerprint"], first_seen=now)
        db.add(host)
    for field in ("hostname", "platform", "os_name", "local_ip", "public_url", "data_dir"):
        setattr(host, field, info[field])
    host.app_version = app_version
    host.last_seen = now
    db.commit()
    db.refresh(host)
    return host


def as_dict(host: AppHost, current: str) -> dict:
    return {
        "id": host.id,
        "hostname": host.hostname,
        "platform": host.platform,
        "os_name": host.os_name,
        "local_ip": host.local_ip,
        "public_url": host.public_url,
        "app_version": host.app_version,
        "data_dir": host.data_dir,
        "first_seen": ist.fmt(host.first_seen),
        "last_seen": ist.fmt(host.last_seen),
        "is_current": host.fingerprint == current,
    }


def history(db: Session) -> list[dict]:
    current = fingerprint()
    rows = db.query(AppHost).order_by(AppHost.last_seen.desc()).all()
    return [as_dict(r, current) for r in rows]
