#!/usr/bin/env python3
"""App — one-step setup and launcher.

Runs on Windows, macOS and Linux using nothing but the Python standard library, so
it can bootstrap a machine that has never seen this app before.

First run asks a handful of questions, installs the dependencies into a private
virtual environment, writes the configuration, and starts the app. Every run after
that just starts it — the answers are remembered in finmate-config.json.

    python3 setup.py                 start (setting up first if needed)
    python3 setup.py --reconfigure   ask the questions again
    python3 setup.py --setup-only    prepare everything but don't start
"""
import base64
import json
import os
import platform
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

MIN_PYTHON = (3, 10)

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV = ROOT / ".venv"
CONFIG = ROOT / "finmate-config.json"
ENV_FILE = BACKEND / ".env"
CARRIED = "carried-secrets.env"   # written by the bundler when data travels along

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------- pretty output
class C:
    """ANSI colours, disabled when the output isn't a terminal that understands them."""
    _on = sys.stdout.isatty() and (not IS_WINDOWS or os.environ.get("WT_SESSION")
                                   or os.environ.get("TERM"))
    B = "\033[1m" if _on else ""
    DIM = "\033[2m" if _on else ""
    G = "\033[32m" if _on else ""
    Y = "\033[33m" if _on else ""
    R = "\033[31m" if _on else ""
    C_ = "\033[36m" if _on else ""
    X = "\033[0m" if _on else ""


def title(text):
    print(f"\n{C.B}{text}{C.X}\n{C.DIM}{'-' * len(text)}{C.X}")


def ok(text):
    print(f"  {C.G}OK{C.X}   {text}")


def warn(text):
    print(f"  {C.Y}!{C.X}    {text}")


def fail(text):
    print(f"  {C.R}X{C.X}    {text}")


def die(text, *hints):
    print()
    fail(text)
    for h in hints:
        print(f"       {C.DIM}{h}{C.X}")
    print()
    if IS_WINDOWS:
        try:
            input("Press Enter to close...")
        except EOFError:
            pass
    sys.exit(1)


# ------------------------------------------------------------------- questions
def ask(question, default=""):
    suffix = f" {C.DIM}[{default}]{C.X}" if default else ""
    while True:
        answer = input(f"  {question}{suffix}: ").strip()
        if answer:
            return answer
        if default:
            return default
        print("       (this one is required)")


def ask_yes_no(question, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"  {question} {C.DIM}[{hint}]{C.X}: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def ask_choice(question, options, default=1):
    """options: list of (label, description). Returns the 1-based index chosen."""
    print(f"\n  {question}")
    for i, (label, desc) in enumerate(options, 1):
        mark = "*" if i == default else " "
        print(f"    {mark} {i}) {C.B}{label}{C.X}")
        if desc:
            print(f"         {C.DIM}{desc}{C.X}")
    while True:
        answer = input(f"  Choose 1-{len(options)} {C.DIM}[{default}]{C.X}: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)


def ask_password(question):
    """Hidden input where the terminal allows it; visible (with a warning) if not."""
    import getpass

    def read(prompt):
        try:
            return getpass.getpass(prompt)
        except (getpass.GetPassWarning, OSError):
            # No console to switch echo off on. Better to keep going with a visible
            # password than to dead-end the whole setup.
            print(f"       {C.Y}(this terminal can't hide typing — your password will show){C.X}")
            return input(prompt)

    while True:
        first = read(f"  {question}: ")
        if len(first) < 12:
            print(f"       {C.Y}Use at least 12 characters.{C.X}")
            continue
        if len(set(first)) < 5:
            print(f"       {C.Y}Too repetitive — mix in more different characters.{C.X}")
            continue
        if read("  Type it again to confirm: ") != first:
            print(f"       {C.Y}They don't match — try again.{C.X}")
            continue
        return first


# ------------------------------------------------------------------ environment
def check_python():
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        have = platform.python_version()
        if IS_WINDOWS:
            hint = "Download it from https://www.python.org/downloads/ and tick 'Add Python to PATH'."
        elif sys.platform == "darwin":
            hint = "Install it with:  brew install python   (or from https://www.python.org/downloads/)"
        else:
            hint = "Install it with your package manager, e.g.  sudo apt install python3 python3-venv"
        die(f"App needs Python {need} or newer — this is Python {have}.", hint)
    ok(f"Python {platform.python_version()} on {platform.system()}")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def venv_works() -> bool:
    """Can this environment actually start?

    A virtual environment records the absolute path of the Python that built it
    and borrows its standard library from there. Carry the folder to another
    computer — or run it from an external drive plugged into a different machine
    — and that path is gone, so python.exe exists but dies the moment it runs.
    Checking the file is present is not the same as checking it works.
    """
    py = venv_python()
    if not py.exists():
        return False
    try:
        r = subprocess.run([str(py), "-c", "import ssl, sqlite3"],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def make_venv():
    if venv_works():
        ok("Virtual environment already set up")
        return
    if venv_python().exists():
        # Built on some other computer, or by a Python that has since been moved
        # or uninstalled. Nothing here is worth keeping — it is all reinstallable
        # and none of your data lives in it.
        print("  The existing Python environment belongs to another computer — rebuilding it...")
        shutil.rmtree(VENV, ignore_errors=True)
    else:
        print("  Creating a private Python environment (this does not touch your system Python)...")
    try:
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    except subprocess.CalledProcessError:
        hint = ("On Debian/Ubuntu you may need:  sudo apt install python3-venv"
                if sys.platform.startswith("linux") else "")
        die("Could not create the virtual environment.", hint)
    ok("Virtual environment created")


def has_internet() -> bool:
    for host in ("pypi.org", "files.pythonhosted.org"):
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            continue
    return False


def pip_install():
    marker = VENV / ".finmate-deps-installed"
    req = BACKEND / "requirements.txt"
    if marker.exists() and marker.read_text().strip() == _digest(req):
        ok("Dependencies already installed")
        return

    if not has_internet():
        die("No internet connection, and the dependencies aren't installed yet.",
            "The very first setup needs to download them from pypi.org.",
            "Connect to the internet and run this again — after that it works offline.")

    print("  Installing dependencies (a few minutes the first time)...")
    py = str(venv_python())
    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"], check=False)
    result = subprocess.run([py, "-m", "pip", "install", "-r", str(req)])
    if result.returncode != 0:
        die("Installing the dependencies failed — see the messages above.",
            "The most common cause is no internet or a proxy blocking pypi.org.")

    # A second pass, deliberately without dependency resolution. The text-reading
    # package declares full opencv_python while this app pins the headless build;
    # letting pip resolve it installs BOTH, so ~60 MB of duplicate OpenCV lands on
    # every machine and two packages end up providing `cv2`. Its real needs are
    # listed in the file itself.
    extra = BACKEND / "requirements-nodeps.txt"
    if extra.exists():
        step = subprocess.run([py, "-m", "pip", "install", "--no-deps", "-r", str(extra)])
        if step.returncode != 0:
            # Not fatal: without this the app runs fine, just without reading text
            # out of documents and photos.
            warn("Text reading (OCR) could not be installed — everything else works.")
    marker.write_text(_digest(req))
    ok("Dependencies installed")


def _digest(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------- configuration
def load_config() -> dict:
    if CONFIG.exists():
        try:
            return _relocate(json.loads(CONFIG.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            warn("finmate-config.json was unreadable — asking again.")
    return {}


def _relocate(cfg: dict) -> dict:
    """Follow the data folder if the bundle itself has moved.

    The recorded path is absolute, which stops being true the moment the folder
    is carried to another computer or sits on an external drive that Windows
    decides to call F: this time instead of E:. When the recorded folder is gone
    but the matching one is sitting right here inside the bundle, that is the
    same data — point at it rather than asking for it again and quietly starting
    an empty database somewhere else.
    """
    old = cfg.get("data_dir")
    if not old or Path(old).exists():
        return cfg
    here = ROOT / Path(old).name
    if not here.exists():
        here = default_data_dir()
    if here.exists():
        cfg["data_dir"] = str(here.resolve())
        print(f"  This folder has moved — using the data in {here}")
    return cfg


def save_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def default_data_dir() -> Path:
    return ROOT / "data"


def interview_gui(cfg: dict) -> dict | None:
    """The setup window. None means it could not run and the prompts should.

    Tried first because most people receiving this app have never opened a
    terminal, and a wall of questions in a black window is where they give up.
    """
    if "--no-gui" in sys.argv:
        return None
    try:
        sys.path.insert(0, str(ROOT))
        import wizard
    except Exception:
        return None

    seed = dict(cfg)
    seed.setdefault("data_dir", str(default_data_dir()))
    # Hosting that came with the bundle removes the whole access question.
    hosted = carried_hosting(Path(seed["data_dir"])) or carried_hosting(bundled_data())
    if hosted:
        seed["_hosted_url"] = hosted["url"]
    # The window needs to know whether a tunnel travelled with the bundle, so it
    # can offer "keep the address this copy came with" instead of demanding a token.
    carried = carried_tunnel(seed)
    if carried:
        seed["_carried_tunnel"] = True
        seed.setdefault("public_url", carried.get("public_url", ""))
    holder = licence_holder(seed)

    out = wizard.run(seed, licensed=holder, needs_account=_needs_account(),
                     default_dir=str(default_data_dir()))
    if out is None:
        return None
    if hosted:
        out["internet"] = "tunnel"
        out["tunnel_token"] = hosted["tunnel_token"]
        out["public_url"] = hosted["url"]
        out.pop("tunnel_id", None)
        return out
    if carried and out.get("internet") == "tunnel":
        out["tunnel_id"] = carried["tunnel_id"]
        out["public_url"] = (carried.get("public_url") or "").rstrip("/")
        out.pop("tunnel_token", None)
        install_carried_tunnel(out)
    return out


def _needs_account() -> bool:
    """Whether setup still has to create the first sign-in."""
    try:
        r = subprocess.run([str(venv_python()), "create_admin.py", "--count"],
                           cwd=str(BACKEND), capture_output=True, text=True, timeout=120)
        return r.returncode == 0 and r.stdout.strip() == "0"
    except (OSError, subprocess.SubprocessError):
        # Before the environment exists there is no way to ask, and assuming an
        # account is needed is the safe guess — ensure_admin() checks again later
        # and skips it if one is already there.
        return True


def interview(cfg: dict) -> dict:
    title("Setting up App")
    print("  Press Enter to accept the suggestion shown in brackets.\n")

    data_dir = Path(ask("Where should your photos and data live?",
                        str(cfg.get("data_dir") or default_data_dir()))).expanduser()
    cfg["data_dir"] = str(data_dir.resolve())

    db_file = data_dir / "finmate.db"
    if db_file.exists():
        size = db_file.stat().st_size / 1048576
        ok(f"Found existing App data here ({size:.1f} MB) — it will be used as-is.")
        cfg["db_engine"] = "sqlite"
    else:
        choice = ask_choice(
            "Which database should App use?",
            [("Built-in (recommended)",
              "Keeps everything in one file inside your data folder. Nothing to install."),
             ("Existing MySQL server",
              "Only if you already run MySQL and want App to use it.")],
            default=1)
        cfg["db_engine"] = "sqlite" if choice == 1 else "mysql"
        if cfg["db_engine"] == "mysql":
            cfg["db_host"] = ask("MySQL host", cfg.get("db_host", "127.0.0.1"))
            cfg["db_port"] = ask("MySQL port", str(cfg.get("db_port", "3306")))
            cfg["db_name"] = ask("Database name", cfg.get("db_name", "finmate"))
            cfg["db_user"] = ask("MySQL username", cfg.get("db_user", "finmate"))
            cfg["db_password"] = ask("MySQL password", cfg.get("db_password", ""))

    cfg["port"] = ask("Which port should App use?", str(cfg.get("port", "8080")))
    cfg["lan"] = ask_yes_no(
        "Allow phones and other devices on your Wi-Fi to open App?",
        cfg.get("lan", True))

    ask_internet(cfg)
    return cfg


def carried_tunnel(cfg: dict) -> dict:
    """The tunnel details that travelled with this copy, if any."""
    path = Path(cfg["data_dir"]) / "cloudflared" / "tunnel.json"
    if not path.exists():
        return {}
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if info.get("tunnel_id") else {}


def install_carried_tunnel(cfg: dict) -> bool:
    """Put the carried credentials where cloudflared looks for them.

    cloudflared only ever reads ~/.cloudflared, so the files have to be placed
    rather than pointed at. Existing files are left alone — this machine may
    already run a tunnel of its own, and overwriting that would break it.
    """
    src = Path(cfg["data_dir"]) / "cloudflared"
    if not src.is_dir():
        return False
    dest = Path.home() / ".cloudflared"
    dest.mkdir(parents=True, exist_ok=True)
    placed = 0
    for path in src.iterdir():
        if path.name == "tunnel.json" or not path.is_file():
            continue
        target = dest / path.name
        if target.exists():
            continue
        shutil.copy2(path, target)
        os.chmod(target, 0o600)   # they authorise the tunnel; don't leave them readable
        placed += 1
    return placed > 0


def ask_internet(cfg: dict):
    """How (and whether) App should be reachable from outside the house.

    A Cloudflare Tunnel is the practical option for a home machine: it dials out, so
    nothing has to be opened on the router and no fixed IP is needed.
    """
    carried = carried_tunnel(cfg)
    previous = cfg.get("internet", "tunnel" if carried else "none")
    default = {"none": 1, "tunnel": 2, "quick": 3}.get(previous, 1)

    if carried:
        # The copy already knows the address it came from, so there is nothing to
        # fetch off the old machine — which is just as well, since by the time
        # this runs the old machine is often already packed away.
        where = carried.get("public_url") or "your existing tunnel"
        own = ("Yes — keep the address this copy came with",
               f"{where} — everything needed travelled with the bundle, "
               "so there is nothing to paste.")
    else:
        own = ("Yes — my own Cloudflare Tunnel",
               "Keeps your own web address, e.g. finmate.yourdomain.com. Needs a token "
               "from the Cloudflare dashboard.")

    choice = ask_choice(
        "Should App be reachable from outside your home network?",
        [("No — this computer and my Wi-Fi only",
          "The safest option. Your phone can still use it over Wi-Fi."),
         own,
         ("Yes — a temporary free link",
          "Cloudflare gives a random address that changes each restart. "
          "No account needed; good for trying it out.")],
        default=default)
    cfg["internet"] = {1: "none", 2: "tunnel", 3: "quick"}[choice]

    if cfg["internet"] == "tunnel" and carried:
        cfg["tunnel_id"] = carried["tunnel_id"]
        cfg.pop("tunnel_token", None)
        cfg["public_url"] = (carried.get("public_url") or "").rstrip("/")
        if install_carried_tunnel(cfg):
            ok("Tunnel credentials installed")
        warn("Only one computer may run this tunnel. Stop App on the old one, "
             "or both will answer the same address from two different databases.")
    elif cfg["internet"] == "tunnel":
        print(f"\n  {C.DIM}In the Cloudflare dashboard: Zero Trust -> Networks -> Tunnels ->")
        print(f"  your tunnel -> Configure. Copy the token from the install command.{C.X}\n")
        cfg["tunnel_token"] = ask("Paste your tunnel token", cfg.get("tunnel_token", ""))
        url = ask("Your public web address", cfg.get("public_url", "https://finmate.example.com"))
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        cfg["public_url"] = url.rstrip("/")
        print(f"\n  {C.DIM}Point that tunnel's public hostname at "
              f"http://localhost:{cfg['port']} in the Cloudflare dashboard.{C.X}")
    elif cfg["internet"] == "quick":
        cfg.pop("tunnel_token", None)
        cfg["public_url"] = ""   # printed by cloudflared once it connects
    else:
        cfg.pop("tunnel_token", None)
        cfg["public_url"] = ""


def bundled_cloudflared() -> Path:
    """Where App keeps its own copy — inside the bundle, so it travels too."""
    return ROOT / "bin" / ("cloudflared.exe" if IS_WINDOWS else "cloudflared")


def find_cloudflared() -> str:
    """Locate cloudflared, including the spots installers use that aren't on PATH."""
    own = bundled_cloudflared()
    if own.exists():
        return str(own)
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
        Path("/opt/homebrew/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


CF_RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download"


def _download(url: str, target: Path):
    """Fetch a file, working around Python's missing certificates on macOS.

    A Python installed from python.org does not trust anything until someone runs
    its "Install Certificates.command", so urllib fails on every HTTPS address
    with a certificate error. Almost nobody has run it. curl ships with macOS and
    with Windows 10 onwards and uses the system's own trust store, so it succeeds
    exactly where urllib fails. Verification stays on in both paths — this
    downloads a program that is about to be run.
    """
    try:
        with urllib.request.urlopen(url, timeout=180) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
        return
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLError):
            raise
        curl = shutil.which("curl")
        if not curl:
            raise
        print("  (using curl — this Python has no certificates installed)")
    subprocess.run([curl, "-fsSL", "--retry", "2", "-o", str(target), url], check=True)


def cloudflared_asset() -> str:
    """The release file for this machine."""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if IS_WINDOWS:
        return "cloudflared-windows-amd64.exe"
    if sys.platform == "darwin":
        # Apple Silicon builds are published; Intel Macs and Rosetta take amd64.
        return f"cloudflared-darwin-{'arm64' if arm else 'amd64'}.tgz"
    return f"cloudflared-linux-{'arm64' if arm else 'amd64'}"


def install_cloudflared() -> str:
    """Fetch cloudflared into the bundle.

    Downloading the binary beats telling someone to install Homebrew or to open an
    Administrator PowerShell: it needs no package manager, no admin rights, and
    touches nothing outside this folder.

    Deliberately not carried in the bundle. The binary is built for one operating
    system and one processor, so the copy that works here is useless on the Mac
    this folder is most likely headed for. Each machine fetches its own.
    """
    asset = cloudflared_asset()
    target = bundled_cloudflared()
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{CF_RELEASE}/{asset}"
    print(f"  Downloading cloudflared for {platform.system()} ({platform.machine()})...")
    try:
        tmp = target.with_suffix(".part")
        _download(url, tmp)
        if asset.endswith(".tgz"):
            # The macOS release is a tarball holding a single "cloudflared" binary.
            with tarfile.open(tmp, "r:gz") as tar:
                member = next((m for m in tar.getmembers()
                               if Path(m.name).name == "cloudflared"), None)
                if member is None:
                    raise ValueError("no cloudflared binary inside the archive")
                extracted = tar.extractfile(member)
                with target.open("wb") as out:
                    shutil.copyfileobj(extracted, out)
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(target)
        if not IS_WINDOWS:
            os.chmod(target, 0o755)
    except Exception as exc:
        warn(f"Could not download cloudflared: {exc}")
        target.unlink(missing_ok=True)
        return ""
    ok("cloudflared installed")
    return str(target)


def ensure_cloudflared() -> str:
    """Find cloudflared, or install it. Only explains itself if both fail."""
    found = find_cloudflared()
    if found:
        return found
    if not has_internet():
        warn("cloudflared isn't installed and there's no internet to fetch it.")
        print("  App will still work on this computer and over your Wi-Fi.\n")
        return ""
    found = install_cloudflared()
    if not found:
        cloudflared_help()
    return found


def cloudflared_help():
    print(f"\n  {C.Y}cloudflared could not be installed automatically.{C.X}")
    print("  App will still work locally and over your Wi-Fi.\n")
    print("  To get the internet address working, install it by hand and run this again:\n")
    if IS_WINDOWS:
        print("    Open PowerShell as Administrator and run:")
        print(f"      {C.B}winget install --id Cloudflare.cloudflared{C.X}")
    elif sys.platform == "darwin":
        print("    In Terminal:")
        print(f"      {C.B}brew install cloudflared{C.X}")
    else:
        print("    See https://developers.cloudflare.com/cloudflare-one/connections/"
              "connect-networks/downloads/")
    print()


# --------------------------------------------------------------------- secrets
def read_carried_secrets(data_dir: Path) -> dict:
    """Secrets copied along with the data. The vault key MUST come across or every
    saved password becomes unreadable — it is the key they were encrypted with."""
    path = data_dir / CARRIED
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _folder_advice(path: str, exc: OSError) -> list:
    """Say why a folder cannot be written to, in terms someone can act on.

    The common one by a distance: a drive formatted on Windows (NTFS) plugged into
    a Mac. macOS mounts those READ-ONLY, so everything looks fine until the first
    write. "Errno 30: Read-only file system" tells the user nothing.
    """
    text = str(path)
    if getattr(exc, "errno", None) == 30 or "read-only" in str(exc).lower():
        advice = ["That location is read-only, so nothing can be saved there."]
        if "/Volumes/" in text or text[1:3] == ":\\" and not text.startswith(("C:", "c:")):
            advice += [
                "External drives formatted on Windows (NTFS) are read-only on a Mac —",
                "macOS can read them but cannot write to them. To use this drive:",
                "  * reformat it as exFAT (this ERASES the drive), or",
                "  * install a driver such as Paragon NTFS for Mac, or",
                "  * choose a folder in your home directory instead.",
            ]
        else:
            advice.append("Choose a different folder, or fix its permissions.")
        return advice
    return [f"The system said: {exc}",
            "Pick a folder you own — somewhere inside your home directory is safest."]


# Files the bundler writes into <bundle>/data. They must follow the customer to
# whatever folder they choose, or the copy arrives licensed and then cannot find
# its own licence.
CARRIED_FILES = ("licence.key", "licence.json", CARRIED, "hosting.json")


def bundled_data() -> Path:
    """The data folder that shipped inside this bundle."""
    return ROOT / "data"


def carry_bundled_files(data_dir: Path) -> None:
    """Move the shipped licence and secrets into the folder actually chosen.

    Without this, picking anything other than the suggested folder silently
    breaks a licensed copy twice over: the licence is not found, so setup asks
    the customer to create an ADMINISTRATOR account, and LICENSE_FILE then points
    at a file that was never put there, so the app refuses to start.
    """
    source = bundled_data()
    try:
        if source.resolve() == Path(data_dir).resolve():
            return
    except OSError:
        return
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in CARRIED_FILES:
        origin = source / name
        target = data_dir / name
        # Never overwrite: a folder that already holds a licence belongs to an
        # earlier install of this same copy, and its licence may be newer.
        if origin.exists() and not target.exists():
            try:
                shutil.copy2(origin, target)
            except OSError:
                pass


def carried_hosting(data_dir: Path) -> dict:
    """A web address provisioned for this copy when it was built.

    When present there is nothing to decide: the address exists, the tunnel token
    is right here, and asking the customer to choose would only give them a way to
    turn off something already paid for.
    """
    path = data_dir / "hosting.json"
    if not path.exists():
        return {}
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if info.get("hostname") and info.get("tunnel_token") else {}


def carried_licence(data_dir: Path) -> dict:
    """Licence details written into the bundle when it was built for a customer."""
    path = data_dir / "licence.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _portable(target: Path) -> str:
    """A path the app can still find after the folder moves.

    Matters most on a USB or external drive: Windows hands out whatever letter is
    free, so the same disk can be E: today and F: tomorrow. An absolute path in
    .env breaks the moment that happens. The backend resolves a relative path
    against its own directory, so anything living inside the bundle is written
    relative and survives the move. A data folder the user put somewhere else
    stays absolute — there is nothing to be relative to.
    """
    try:
        return os.path.relpath(target, BACKEND).replace("\\", "/")
    except ValueError:
        return target.as_posix()      # different drive entirely — keep it absolute


def write_env(cfg: dict):
    """Write backend/.env once. Existing files are left alone so the vault key and
    session secrets stay stable between runs."""
    if ENV_FILE.exists():
        ok("Configuration already written (backend/.env)")
        return

    data_dir = Path(cfg["data_dir"])
    media_dir = data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    carried = read_carried_secrets(data_dir)

    vault_key = carried.get("VAULT_KEY_HEX") or secrets.token_hex(32)
    if carried.get("VAULT_KEY_HEX"):
        ok("Reusing the vault key that came with your data (your saved passwords stay readable)")

    lines = [
        "# App configuration — generated by setup.py.",
        "# Treat this file like a password: it holds the key your vault is encrypted with.",
        "",
        f"DB_ENGINE={cfg['db_engine']}",
    ]
    if cfg["db_engine"] == "sqlite":
        lines += [f"DB_FILE={_portable(data_dir / 'finmate.db')}"]
    else:
        lines += [
            f"DB_HOST={cfg.get('db_host', '127.0.0.1')}",
            f"DB_PORT={cfg.get('db_port', '3306')}",
            f"DB_NAME={cfg.get('db_name', 'finmate')}",
            f"DB_USER={cfg.get('db_user', 'finmate')}",
            f"DB_PASSWORD={cfg.get('db_password', '')}",
        ]
    lines += [
        f"MEDIA_ROOT={_portable(media_dir)}",
        "",
        "# Session tokens — regenerated per machine on purpose; everyone signs in again.",
        f"JWT_SECRET={secrets.token_urlsafe(48)}",
        f"MEDIA_SECRET={secrets.token_urlsafe(48)}",
        "",
        "# Vault encryption. Never change this by hand once data exists.",
        f"VAULT_KEY_HEX={vault_key}",
    ]
    if carried.get("VAULT_KEY_LEGACY_HEX"):
        lines.append(f"VAULT_KEY_LEGACY_HEX={carried['VAULT_KEY_LEGACY_HEX']}")
    if carried.get("VAPID_PUBLIC_KEY") and carried.get("VAPID_PRIVATE_KEY"):
        lines += ["", "# Push notification keys carried over with your data.",
                  f"VAPID_PUBLIC_KEY={carried['VAPID_PUBLIC_KEY']}",
                  f"VAPID_PRIVATE_KEY={carried['VAPID_PRIVATE_KEY']}"]
    if cfg.get("public_url"):
        lines += ["", "# The address this copy is reached at from the internet.",
                  f"PUBLIC_BASE_URL={cfg['public_url']}"]

    licence = carried_licence(data_dir) or carried_licence(bundled_data())
    if licence.get("licensed"):
        # Note what is NOT written here: LICENSE_SIGNING_KEY_HEX. The public half
        # verifies a licence; the private half issues them. A copy that could
        # issue its own would make the expiry date a suggestion.
        lines += ["", "# This copy runs under a licence and stops when it expires.",
                  "LICENSED_MODE=true",
                  f"LICENSE_PUBLIC_KEY_HEX={licence.get('public_key', '')}",
                  f"LICENSE_FILE={_portable(data_dir / 'licence.key')}"]
        if licence.get("check_url"):
            lines.append(f"LICENSE_CHECK_URL={licence['check_url']}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)  # no-op on Windows, meaningful on macOS/Linux
    except OSError:
        pass
    ok("Configuration written (backend/.env)")


def ensure_vision_models():
    """Face grouping and photo search need ~190 MB of models. A bundle normally
    carries them; a fresh checkout does not, so offer to fetch them once."""
    marker = BACKEND / "models" / "face_recognition_sface_2021dec.onnx"
    clip = BACKEND / "models" / "clip" / "vision_model_quantized.onnx"
    if marker.exists() and clip.exists():
        ok("Photo grouping and search models are present")
        return
    print()
    print("  App can group photos by face and let you search by what's IN a")
    print("  picture. That needs a one-off ~190 MB download, and works offline after.")
    if not ask_yes_no("Download the photo models now?", True):
        warn("Skipped — the Gallery still works, just without those two features.")
        warn("Run  python backend/download_models.py  any time to add them.")
        return
    result = subprocess.run([str(venv_python()), "download_models.py"], cwd=str(BACKEND))
    if result.returncode != 0:
        warn("Some models did not download; those features stay off for now.")


# ----------------------------------------------------------------- first account
def licence_holder(cfg: dict) -> dict:
    """Name and email from the licence, when this is a licensed copy.

    Read straight out of the signed token so the account cannot be set up under
    a different name than the one the licence was sold to.
    """
    # Look in the chosen folder first, then in the one that shipped with the
    # bundle. On a first run those differ whenever the customer picked their own
    # location, and only the shipped folder has the licence at that point.
    where = Path(cfg.get("data_dir") or bundled_data())
    if not (where / "licence.key").exists():
        where = bundled_data()
    licence = carried_licence(where)
    if not licence.get("licensed"):
        return {}
    try:
        token = (where / "licence.key").read_text(encoding="utf-8").strip()
        body = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return {"name": payload.get("name", ""), "email": payload.get("email", "")}
    except Exception:
        return {}


def ensure_admin(cfg: dict, account: dict | None = None):
    """Create the first account if the database has no users yet.

    A licensed copy gets exactly one account, named by the licence and holding no
    administrator rights: the customer runs the app, they do not run a App
    installation. Everything else — an ordinary copy someone is moving between
    their own machines — still gets a full administrator.

    `account` is what the setup window already collected. It is only ever held in
    memory and is never written to the config file.
    """
    result = subprocess.run([str(venv_python()), "create_admin.py", "--count"],
                            cwd=str(BACKEND), capture_output=True, text=True)
    if result.returncode != 0:
        die("Could not open the database.", result.stderr.strip()[-600:])
    if result.stdout.strip() != "0":
        ok(f"Database ready ({result.stdout.strip()} user account(s) already set up)")
        return

    if account and account.get("email") and account.get("password"):
        script = "create_account.py" if account.get("role") == "user" else "create_admin.py"
        args = [script, account["name"], account["email"], account["password"]]
        if script == "create_account.py":
            args.append("user")
        result = subprocess.run([str(venv_python()), *args],
                                cwd=str(BACKEND), capture_output=True, text=True)
        if result.returncode != 0:
            die("Could not create the account.", result.stdout.strip(),
                result.stderr.strip()[-400:])
        ok(f"Account created — sign in as {account['email']}")
        return

    holder = licence_holder(cfg)
    if holder.get("email"):
        title("Set your password")
        print(f"  This copy is licensed to {C.B}{holder['name']}{C.X} "
              f"({holder['email']}).")
        print("  Choose the password you'll sign in with.\n")
        password = ask_password("Choose a password (12+ characters)")
        args = ["create_account.py", holder["name"], holder["email"], password, "user"]
    else:
        title("Create your administrator account")
        print("  This is the account you'll sign in with. Nothing leaves this machine.\n")
        name = ask("Your name", "Admin")
        email = ask("Email address (used as your username)")
        password = ask_password("Choose a password (12+ characters)")
        args = ["create_admin.py", name, email, password]

    result = subprocess.run([str(venv_python()), *args],
                            cwd=str(BACKEND), capture_output=True, text=True)
    if result.returncode != 0:
        die("Could not create the account.", result.stdout.strip(), result.stderr.strip()[-400:])
    ok(f"Account created — sign in as {args[2]}")


# ----------------------------------------------------------------------- start
def lan_address() -> str:
    """Best-effort LAN IP. No packets are sent — connect() on UDP just picks a route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_tunnel(cfg: dict):
    """Launch cloudflared next to the app. Returns the process, or None."""
    mode = cfg.get("internet", "none")
    if mode == "none":
        return None
    exe = ensure_cloudflared()
    if not exe:
        return None

    port = str(cfg.get("port", "8080"))
    if mode == "tunnel":
        tunnel_id = (cfg.get("tunnel_id") or "").strip()
        token = (cfg.get("tunnel_token") or "").strip()
        if tunnel_id:
            # Credentials that came with the bundle. --url is required: a tunnel
            # set up from the command line keeps no ingress rules in Cloudflare's
            # dashboard, so without it there is nowhere to route and every request
            # comes back as error 1033.
            cmd = [exe, "tunnel", "--no-autoupdate",
                   "--url", f"http://127.0.0.1:{port}", "run", tunnel_id]
        elif token:
            cmd = [exe, "tunnel", "--no-autoupdate",
                   "--url", f"http://127.0.0.1:{port}", "run", "--token", token]
        else:
            warn("No tunnel details saved — skipping the internet address.")
            return None
    else:
        cmd = [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]

    try:
        # cloudflared writes its status (including the random quick-tunnel URL) to
        # stderr; leaving it attached means the user sees the address appear.
        return subprocess.Popen(cmd)
    except OSError as exc:
        warn(f"Could not start cloudflared: {exc}")
        return None


def run_app(cfg: dict):
    port = str(cfg.get("port", "8080"))
    host = "0.0.0.0" if cfg.get("lan", True) else "127.0.0.1"
    local = f"http://127.0.0.1:{port}"

    title("Starting App")
    print(f"  On this computer : {C.C_}{local}{C.X}")
    if cfg.get("lan", True):
        print(f"  On your phone    : {C.C_}http://{lan_address()}:{port}{C.X}")
        print(f"  {C.DIM}(the phone must be on the same Wi-Fi){C.X}")
    if cfg.get("public_url"):
        print(f"  From anywhere    : {C.C_}{cfg['public_url']}{C.X}")
    elif cfg.get("internet") == "quick":
        print(f"  From anywhere    : {C.DIM}a temporary link appears below once "
              f"Cloudflare connects{C.X}")
    print(f"\n  {C.DIM}Leave this window open. Press Ctrl+C to stop App.{C.X}\n")

    tunnel = start_tunnel(cfg)
    threading.Thread(target=lambda: (time.sleep(3), webbrowser.open(local)),
                     daemon=True).start()
    try:
        # --no-access-log: a request log line per hit will block the whole app
        # if its output has nowhere to drain, which is what a hidden console does.
        # --no-server-header: uvicorn otherwise announces itself in every
        # response, which tells a scanner exactly which exploits to try. It adds
        # the header after our middleware runs, so it can only be stopped here.
        subprocess.run([str(venv_python()), "-m", "uvicorn", "app.main:app",
                        "--no-server-header",
                        "--host", host, "--port", port, "--no-access-log"],
                       cwd=str(BACKEND))
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}App stopped.{C.X}")
    finally:
        if tunnel and tunnel.poll() is None:
            # Leaving cloudflared running would keep publishing a port that no
            # longer answers, so it goes down with the app.
            tunnel.terminate()
            try:
                tunnel.wait(timeout=8)
            except subprocess.TimeoutExpired:
                tunnel.kill()


# ------------------------------------------------------------------------ main
def _prepare(cfg: dict, account: dict | None) -> None:
    """Everything between answering the questions and the app being ready to run."""
    make_venv()
    pip_install()
    try:
        Path(cfg["data_dir"]).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        die(f"App cannot write to {cfg['data_dir']}", *_folder_advice(cfg["data_dir"], exc))
    # Before write_env: it records LICENSE_FILE against this folder, so the
    # licence has to be in it by then.
    carry_bundled_files(Path(cfg["data_dir"]))
    write_env(cfg)
    ensure_vision_models()
    ensure_admin(cfg, account)


def main() -> int:
    args = set(sys.argv[1:])
    print(f"\n{C.B}  App{C.X} {C.DIM}— personal finance, documents and photos{C.X}")

    if not BACKEND.is_dir() or not (ROOT / "frontend" / "dist" / "index.html").exists():
        die("This doesn't look like a complete App folder.",
            f"Expected 'backend' and 'frontend/dist' next to {Path(__file__).name}.",
            "Copy the whole folder, not just this file.")

    title("Checking your computer")
    check_python()

    cfg = load_config()
    first_run = not ENV_FILE.exists() or "--reconfigure" in args
    if first_run:
        if "--reconfigure" in args and ENV_FILE.exists():
            keep = ask_yes_no("Keep the existing secrets and database settings?", True)
            if not keep:
                ENV_FILE.unlink()
        cfg = interview_gui(cfg) or interview(cfg)
        # The password never reaches finmate-config.json. That file is plain text,
        # sits next to the app, and gets copied along with the folder — a password
        # written there would travel to every machine the bundle is moved to.
        account = cfg.pop("_account", None)
        save_config(cfg)
    else:
        account = None
        ok("Already configured — starting up")

    title("Preparing")
    prepare = lambda: _prepare(cfg, account)   # noqa: E731 — passed to the progress window

    # Build the progress window separately from running the work. Wrapping both in
    # one try meant a genuine failure in the work was caught and then the whole
    # thing was run a SECOND time — installing everything twice and printing the
    # same traceback twice. Only a window that cannot be created is a reason to
    # fall back here.
    progress = None
    if account is not None:
        try:
            sys.path.insert(0, str(ROOT))
            import wizard
            candidate = wizard.Progress()
            progress = candidate if getattr(candidate, "_ok", False) else None
        except Exception:
            progress = None

    if progress is not None:
        progress.run_until(prepare)
    else:
        prepare()

    if "--setup-only" in args:
        print(f"\n  {C.G}Setup complete.{C.X} Start it any time with the launcher in this folder.\n")
        return 0
    run_app(cfg)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(130)
