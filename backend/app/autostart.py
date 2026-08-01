"""Start SafeNest when the computer starts, so it can act as the owner's server.

THE POINT OF THIS FILE
The product's promise is that your own machine holds your records and you can
reach them from anywhere while it is switched on. A program that only runs when
somebody double-clicks it cannot keep that promise: the customer reboots, the
address stops answering, and they have no idea why. This registers the app to
start at login so "the laptop is on" is the only condition that matters.

NO ADMINISTRATOR RIGHTS
Everything here is per-user. A licensed customer is deliberately given the `user`
role and may well not be an administrator on their own machine either, so any
mechanism needing elevation would be unavailable to exactly the people who need
this. That rules out Windows services and /Library/LaunchDaemons, and leaves:

  Windows   a .cmd in the per-user Startup folder
  macOS     a LaunchAgent plist in ~/Library/LaunchAgents

Both are plain files the owner can see, read and delete without tooling — which
matters for a product whose whole argument is that nothing is hidden from you.
"""
import os
import platform
import subprocess
import sys
from pathlib import Path

APP_ID = "com.safenest.app"          # LaunchAgent label; also the plist filename
_WINDOWS = os.name == "nt"
_MAC = sys.platform == "darwin"


def supported() -> bool:
    return _WINDOWS or _MAC


def _frozen() -> bool:
    """True inside the packaged build, where sys.executable IS the app."""
    return bool(getattr(sys, "frozen", False))


def launch_command() -> list[str]:
    """What to run at login.

    In a packaged copy that is the executable itself. From source it is the same
    uvicorn line the developer would type, so this can be tested without building
    an executable first.
    """
    if _frozen():
        return [str(Path(sys.executable).resolve())]
    backend = Path(__file__).resolve().parent.parent
    python = backend / "venv" / ("Scripts/python.exe" if _WINDOWS else "bin/python")
    exe = str(python if python.exists() else sys.executable)
    return [exe, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", "8080", "--no-access-log", "--no-server-header"]


def _working_dir() -> str:
    if _frozen():
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parent.parent)


# --------------------------------------------------------------------- Windows
def _startup_dir() -> Path:
    return (Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
            / "Microsoft/Windows/Start Menu/Programs/Startup")


def _cmd_file() -> Path:
    return _startup_dir() / "SafeNest.cmd"


def _win_enable() -> None:
    cmd = launch_command()
    # `start "" /min` hands off and returns, so the login sequence is not held up
    # waiting for a server that never exits. The empty "" is the window title
    # argument — without it `start` treats a quoted path AS the title and opens a
    # console instead of the program, which is a genuinely baffling failure.
    quoted = " ".join(f'"{c}"' for c in cmd)
    _startup_dir().mkdir(parents=True, exist_ok=True)
    _cmd_file().write_text(
        "@echo off\r\n"
        "REM Starts SafeNest at login so this computer can serve your records.\r\n"
        "REM Delete this file to stop that happening.\r\n"
        f'cd /d "{_working_dir()}"\r\n'
        f"start \"\" /min {quoted}\r\n",
        encoding="utf-8", newline="")


def _win_disable() -> None:
    try:
        _cmd_file().unlink()
    except FileNotFoundError:
        pass


def _win_enabled() -> bool:
    return _cmd_file().is_file()


# ------------------------------------------------------------------------- Mac
def _plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{APP_ID}.plist"


def _mac_enable() -> None:
    args = "".join(f"    <string>{c}</string>\n" for c in launch_command())
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        f'  <key>Label</key><string>{APP_ID}</string>\n'
        '  <key>ProgramArguments</key>\n  <array>\n'
        f'{args}'
        '  </array>\n'
        f'  <key>WorkingDirectory</key><string>{_working_dir()}</string>\n'
        '  <key>RunAtLoad</key><true/>\n'
        # KeepAlive restarts it if it ever stops. That is the difference between
        # "runs at login" and "is a server": a crash at 2am must not mean the
        # address is dead until somebody notices.
        '  <key>KeepAlive</key><true/>\n'
        '  <key>ProcessType</key><string>Background</string>\n'
        '</dict>\n</plist>\n', encoding="utf-8")
    # bootstrap is the modern form; load -w still works on older systems, so try
    # the new one and fall back rather than requiring a particular macOS.
    uid = os.getuid()  # type: ignore[attr-defined]
    for cmd in (["launchctl", "bootstrap", f"gui/{uid}", str(path)],
                ["launchctl", "load", "-w", str(path)]):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            if r.returncode == 0:
                return
        except Exception:
            continue


def _mac_disable() -> None:
    path = _plist_path()
    uid = os.getuid()  # type: ignore[attr-defined]
    for cmd in (["launchctl", "bootout", f"gui/{uid}/{APP_ID}"],
                ["launchctl", "unload", "-w", str(path)]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
        except Exception:
            pass
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _mac_enabled() -> bool:
    return _plist_path().is_file()


# ------------------------------------------------------------------------- API
def status() -> dict:
    """Whether the app is set to start at login, and where that is recorded."""
    if not supported():
        return {"supported": False, "enabled": False, "platform": platform.system(),
                "reason": "Starting at login is only set up for Windows and macOS."}
    enabled = _win_enabled() if _WINDOWS else _mac_enabled()
    where = str(_cmd_file() if _WINDOWS else _plist_path())
    return {
        "supported": True,
        "enabled": enabled,
        "platform": "windows" if _WINDOWS else "mac",
        # Shown to the owner: this is a file they can delete themselves, and
        # saying where it is keeps the mechanism honest.
        "path": where,
        "needs_admin": False,
    }


def enable() -> dict:
    if not supported():
        raise RuntimeError("Starting at login is only set up for Windows and macOS.")
    _win_enable() if _WINDOWS else _mac_enable()
    return status()


def disable() -> dict:
    if not supported():
        raise RuntimeError("Starting at login is only set up for Windows and macOS.")
    _win_disable() if _WINDOWS else _mac_disable()
    return status()


def ensure_default(marker_dir) -> bool:
    """Turn this on once, the first time a licensed copy runs.

    A customer bought a personal server; making them find a setting before it
    behaves like one would be a poor first day. So a licensed copy switches
    itself on at first launch and says so on the Profile screen.

    The marker means "we have made this decision once" — not "it is on". Someone
    who turns it off must stay off, and re-enabling it on every boot would be the
    kind of software that ignores you.
    """
    marker = Path(marker_dir) / ".autostart-set"
    if marker.exists() or not supported():
        return False
    try:
        enable()
    except Exception as exc:
        print(f"[autostart] could not enable: {exc}")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("decided\n", encoding="utf-8")
    except OSError:
        pass
    return True
