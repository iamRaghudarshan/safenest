"""Keep the Cloudflare tunnel running alongside the app.

WHY THE APP SUPERVISES IT
The product's promise is "reachable from anywhere while your computer is on".
That needs two processes alive, not one — the app and the connector. Asking a
customer to arrange that themselves means asking them to install a Windows
service (which needs administrator rights they may not have) or to remember to
start a second program after every reboot. Either way the address goes dark and
they do not know why.

So the app starts the connector itself when one is configured, and restarts it if
it dies. One thing to set up at login instead of two.

It is deliberately quiet about failure: no tunnel simply means the app is
reachable on the local network, which is a perfectly good state and not an error
worth interrupting anybody over.
"""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

_proc: subprocess.Popen | None = None
_stop = threading.Event()

# Where cloudflared installs itself on each platform, in the order worth trying.
_CANDIDATES = [
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
    "/opt/homebrew/bin/cloudflared",
    "/usr/local/bin/cloudflared",
    "/usr/bin/cloudflared",
]


def binary() -> str:
    """The connector, or "" when it is not installed."""
    found = shutil.which("cloudflared")
    if found:
        return found
    for path in _CANDIDATES:
        if Path(path).is_file():
            return path
    return ""


def config_path() -> Path:
    return Path(os.path.expanduser("~")) / ".cloudflared" / "config.yml"


def stored_token() -> str:
    """The connector token saved by the automatic setup, if there is one.

    A tunnel created with `config_src: cloudflare` keeps its routing at
    Cloudflare, so the connector needs nothing on disk but this string — no
    cert.pem, no credentials JSON, no config.yml. That is the whole reason the
    automatic setup can skip the terminal.
    """
    try:
        from .database import SessionLocal
        from . import weburl
        db = SessionLocal()
        try:
            return (weburl._row(db).tunnel_token or "").strip()
        finally:
            db.close()
    except Exception:
        return ""


def _command() -> list | None:
    """How to start the connector, or None when there is nothing to start.

    Token first: it is the arrangement the app sets up itself and needs no files.
    The config file remains supported for installations that were set up by hand
    before this existed, and for anyone who prefers to manage it themselves.
    """
    exe = binary()
    if not exe:
        return None
    token = stored_token()
    if token:
        flags = ["--no-autoupdate"] if os.name == "nt" else []
        return [exe, "tunnel", *flags, "run", "--token", token]
    if config_path().is_file():
        return [exe, "--config", str(config_path()), "tunnel", "run"]
    return None


def configured(db=None) -> tuple[bool, str]:
    """Is there a tunnel to run? Returns (yes, why-not)."""
    if not binary():
        return False, "cloudflared is not installed on this computer"
    if _command() is None:
        return False, "no tunnel set up yet"
    return True, ""


def _run_forever() -> None:
    """Start the connector and keep it up, backing off if it fails repeatedly."""
    global _proc
    delay = 5
    while not _stop.is_set():
        ok, _why = configured()
        if not ok:
            # Nothing to do yet. Re-check occasionally: the owner may set the
            # tunnel up from the Web address screen while the app is running.
            if _stop.wait(60):
                return
            continue
        try:
            flags = 0
            if os.name == "nt":
                # No console window: this is a background helper, and a black box
                # appearing at every login looks like something went wrong.
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            cmd = _command()
            if cmd is None:          # set up, then unset, between the two checks
                if _stop.wait(60):
                    return
                continue
            _proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags)
            print("[tunnel] connector started")
            _proc.wait()
            if _stop.is_set():
                return
            print("[tunnel] connector exited — restarting")
            delay = 5
        except Exception as exc:
            print(f"[tunnel] could not start the connector: {exc}")
            # Back off so a permanent problem (missing binary, bad config) does
            # not spin. Capped, because the cause may be temporary.
            delay = min(delay * 2, 300)
        if _stop.wait(delay):
            return


_thread: threading.Thread | None = None


def start() -> None:
    """Begin supervising, in the background. Safe to call when nothing is set up.

    Idempotent on purpose: restart() calls it, and a second supervisor thread
    would race the first over `_proc` and leave an orphaned connector running.
    """
    global _thread
    if os.environ.get("SAFENEST_NO_TUNNEL"):
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run_forever, daemon=True, name="tunnel")
    _thread.start()


def stop() -> None:
    _stop.set()
    if _proc and _proc.poll() is None:
        try:
            _proc.terminate()
        except Exception:
            pass


def restart() -> None:
    """Pick up a tunnel that was just set up, without waiting for the app to close.

    Only ever kills the connector this app started — a child process of ours. The
    Windows service, if somebody set one up by hand, is left alone: stopping a
    service from inside a web request is the thing the Web address screen has
    always refused to do, and for the same reason.
    """
    global _proc
    proc, _proc = _proc, None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
    # If the supervisor is already looping it sees the process exit and starts the
    # new command on its next pass. start() is idempotent, so this only matters
    # when nothing was supervising yet — the usual case right after setup.
    start()


def status() -> dict:
    ok, why = configured()
    running = bool(_proc and _proc.poll() is None)
    return {
        "installed": bool(binary()),
        "configured": ok,
        "running": running,
        "config_path": str(config_path()),
        "reason": "" if ok else why,
    }
