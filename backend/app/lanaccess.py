"""Let phones on the same Wi-Fi actually reach this computer.

THE PROBLEM THIS SOLVES
The launcher prints two addresses -- one for this computer, one for your phone --
and the phone one simply did not work. The server was never at fault: uvicorn
binds 0.0.0.0, so it listens on every interface. Windows Firewall silently drops
the inbound connection before it arrives.

A first-run firewall prompt is supposed to cover this, but it only appears for a
program with a visible window in the foreground, and the app is usually launched
from a shortcut or at login by autostart.py. So most people never see the prompt,
and the address they were told to use on their phone just times out with nothing
anywhere explaining why. Printing a longer message would not fix it -- the person
holding the phone cannot act on a firewall rule they do not know how to write.

WHAT IT DOES
Adds one inbound TCP rule for this executable on private networks only. Public
networks -- cafes, airports -- are deliberately left alone: this is a machine
holding somebody's financial records, and it should not answer strangers on a
hotel network because they once wanted it on their phone at home.

ELEVATION
Creating a firewall rule needs administrator rights, which per autostart.py's
reasoning a licensed customer may not have. So it is attempted unelevated first
(it succeeds if they are an admin), and only then falls back to one UAC prompt.
If both fail, `advice()` gives the person something they can actually do.

macOS has no equivalent problem: the application firewall is off by default and
allows incoming connections for signed binaries when on.
"""
import os
import subprocess
import sys
from pathlib import Path

_WINDOWS = os.name == "nt"
RULE_PREFIX = "SafeNest LAN"      # a literal, not the brand: see _rule_name()


def _no_window() -> dict:
    """Keep netsh from flashing a console window over the app."""
    if not _WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": si, "creationflags": 0x08000000}   # CREATE_NO_WINDOW


def _exe() -> str:
    """The program the rule is for -- the packaged .exe, or python when running
    from source (where the rule is a developer convenience, not a shipped one)."""
    return str(Path(sys.executable).resolve())


def _rule_name() -> str:
    """Stable across renames, on purpose.

    The rule is keyed to the executable, not the brand. Naming it after the app
    would leave an orphaned rule behind every time somebody renamed their copy,
    and Windows would accumulate one per name it had ever had.
    """
    return f"{RULE_PREFIX} ({Path(_exe()).stem})"


def exists() -> bool:
    if not _WINDOWS:
        return True
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             f"name={_rule_name()}"],
            capture_output=True, text=True, timeout=15, **_no_window())
        return r.returncode == 0 and "No rules match" not in (r.stdout or "")
    except Exception:
        return False


def _add_command() -> list:
    return ["netsh", "advfirewall", "firewall", "add", "rule",
            f"name={_rule_name()}", "dir=in", "action=allow",
            f"program={_exe()}", "enable=yes",
            # Private only. See the module docstring: a records machine has no
            # business answering an airport network.
            "profile=private", "protocol=TCP"]


def ensure(elevate: bool = False) -> tuple[bool, str]:
    """Make sure the rule is there. Returns (allowed, how).

    `elevate` is opt-in because a UAC dialog appearing unrequested, at login, on
    a machine somebody was handed, is alarming rather than helpful.
    """
    if not _WINDOWS:
        return True, "not needed on this platform"
    if exists():
        return True, "already allowed"
    try:
        r = subprocess.run(_add_command(), capture_output=True, text=True,
                           timeout=20, **_no_window())
        if r.returncode == 0:
            return True, "added"
    except Exception:
        pass
    if not elevate:
        return False, "needs administrator"
    try:
        import ctypes
        args = " ".join(f'"{a}"' if " " in a else a for a in _add_command()[1:])
        # ShellExecuteW with "runas" is the only way to raise a UAC prompt from a
        # process that is not already elevated.
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "netsh", args, None, 0)
        if int(rc) > 32:
            return exists(), "added with permission"
        return False, "permission refused"
    except Exception as exc:
        return False, f"could not ask: {exc}"


def remove() -> bool:
    if not _WINDOWS:
        return True
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={_rule_name()}"],
            capture_output=True, text=True, timeout=20, **_no_window())
        return r.returncode == 0
    except Exception:
        return False


def _powershell(script: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25, **_no_window())
        return r.returncode, (r.stdout or "").strip()
    except Exception:
        return 1, ""


def networks() -> list:
    """Each connected network and how Windows has classified it.

    This is the other half of the problem, and the half nobody guesses. Windows
    marks most newly joined networks **Public**, and a Public network ignores a
    private-profile firewall rule entirely -- so the rule is present, correct, and
    does nothing. The symptom is identical to having no rule at all: the phone
    just times out.
    """
    if not _WINDOWS:
        return []
    rc, out = _powershell(
        "Get-NetConnectionProfile | ForEach-Object "
        "{ $_.Name + '|' + $_.NetworkCategory }")
    if rc != 0 or not out:
        return []
    rows = []
    for line in out.splitlines():
        name, _, cat = line.partition("|")
        if name.strip():
            rows.append({"name": name.strip(), "category": cat.strip() or "Unknown"})
    return rows


def on_private_network() -> bool:
    """True when at least one connected network is Private or Domain."""
    if not _WINDOWS:
        return True
    nets = networks()
    if not nets:
        return True     # cannot tell; do not claim a problem that may not exist
    return any(n["category"] in ("Private", "DomainAuthenticated", "Domain")
               for n in nets)


def make_private(elevate: bool = True) -> tuple[bool, str]:
    """Reclassify the connected networks as Private.

    Offered rather than done quietly. Marking a network Private is the correct
    thing on somebody's home Wi-Fi and the wrong thing in a hotel, and only the
    person standing there knows which one they are on.
    """
    if not _WINDOWS:
        return True, "not needed on this platform"
    script = ("Get-NetConnectionProfile | Where-Object "
              "{ $_.NetworkCategory -eq 'Public' } | "
              "Set-NetConnectionProfile -NetworkCategory Private")
    rc, _ = _powershell(script)
    if rc == 0 and on_private_network():
        return True, "changed"
    if not elevate:
        return False, "needs administrator"
    try:
        import ctypes
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell",
            f'-NoProfile -NonInteractive -Command "{script}"', None, 0)
        if int(rc) > 32:
            import time
            time.sleep(1.5)      # the elevated process runs detached
            return on_private_network(), "changed with permission"
        return False, "permission refused"
    except Exception as exc:
        return False, f"could not ask: {exc}"


def reachable() -> bool:
    """Can a phone on the same Wi-Fi actually get through right now?

    Both conditions, because either one alone is silently useless.
    """
    return exists() and on_private_network()


def lan_address() -> str:
    """This machine's address on the local network, or "" if it has none.

    Asks the routing table which interface reaches the internet rather than
    trusting the hostname: on a laptop with a VPN or Docker installed,
    gethostbyname() routinely returns a virtual adapter nothing else can reach.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))       # no packet is sent by a UDP connect
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def lan_url(port: int | None = None) -> str:
    ip = lan_address()
    if not ip:
        return ""
    return f"http://{ip}:{port or int(os.environ.get('PORT', '8080'))}"


def advice(port: int) -> str:
    """What to tell someone whose phone cannot reach the app.

    Names the actual command rather than describing the Windows Firewall UI:
    it is one line they can paste, and it does not go stale when Microsoft moves
    the setting again.
    """
    if not _WINDOWS:
        return ""
    lines = ["  Your phone cannot reach this computer yet:"]
    if not exists():
        lines += ["",
                  "  * Windows Firewall is blocking it. In Command Prompt as",
                  "    administrator, run:",
                  "",
                  f'      netsh advfirewall firewall add rule name="{_rule_name()}"',
                  f'        dir=in action=allow program="{_exe()}" enable=yes',
                  "        profile=private protocol=TCP"]
    if not on_private_network():
        nets = ", ".join(n["name"] for n in networks()) or "your Wi-Fi"
        lines += ["",
                  f"  * Windows treats {nets} as a Public network, which hides this",
                  "    computer from your own devices. In Settings > Network &",
                  "    internet > Wi-Fi, open the network and choose Private.",
                  "    Only do this on a network you trust."]
    lines += ["",
              "  Both can be done from inside the app: Profile > On my Wi-Fi.\n"]
    return "\n".join(lines)
