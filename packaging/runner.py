"""Entry point for the packaged FinMate executable.

Inside a PyInstaller build there is no `python setup.py` and no virtual
environment — the interpreter, the libraries and this file are all one program.
So the jobs setup.py does at runtime (find the data folder, write configuration,
start uvicorn, open a browser) have to happen here instead, minus everything to
do with installing, which already happened at build time.
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def bundle_dir() -> Path:
    """Where the packaged files live (read-only once installed)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)      # PyInstaller's extraction/base directory
    return Path(__file__).resolve().parent.parent


def in_app_bundle() -> bool:
    """Running from a macOS .app rather than a plain folder."""
    return ".app/Contents/MacOS" in str(Path(sys.executable).resolve())


def install_dir() -> Path:
    """The folder that owns this copy's records.

    Deliberately not _MEIPASS: in one-file mode that is a temporary directory
    that gets deleted on exit, which would silently discard every record the
    moment the app closed.

    Inside a macOS .app it is not the executable's folder either. Writing into an
    .app breaks its code signature, and everything in there is replaced wholesale
    by an update or a drag to Applications -- a customer's whole history would go
    with it. macOS keeps that sort of thing in Application Support, so that is
    where it goes, and the .app stays read-only as Apple intends.
    """
    if getattr(sys, "frozen", False):
        if in_app_bundle():
            name = Path(sys.executable).resolve().parent.parent.parent.stem
            root = Path.home() / "Library" / "Application Support" / (name or "SafeNest")
            root.mkdir(parents=True, exist_ok=True)
            return root
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def seed_dir() -> Path | None:
    """Where a .app carries the licence it shipped with, read-only.

    The .app cannot be written to, so the licence, the branded database and the
    vault key travel inside it and are copied out to Application Support the first
    time it runs. Without this a customer's copy would start with no licence at
    all and refuse to work.
    """
    if not (getattr(sys, "frozen", False) and in_app_bundle()):
        return None
    contents = Path(sys.executable).resolve().parent.parent
    seed = contents / "Resources" / "seed-data"
    return seed if seed.is_dir() else None


def pause() -> None:
    """Hold the window open so an error can be read — where there is a window.

    Calling input() to report a failure is a trap: if the failure was that there
    is no console, input() raises the very same error and the message the user
    needed is replaced by a traceback they cannot act on.
    """
    try:
        if sys.stdin and sys.stdin.isatty():
            input("  Press Enter to close...")
    except (EOFError, OSError):
        pass


def free_port(preferred: int = 8080) -> int:
    for port in (preferred, *range(8081, 8100)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def lan_address() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


POINTER = "data-location.txt"


def early_brand() -> str:
    """The app's name, using nothing but the executable.

    Deliberately NOT brand(): everything in this file that runs before
    prepare_environment() is running before the app's secrets exist, and importing
    `app` at that point raises SystemExit out of config.py. That is not catchable
    by `except Exception`, so it does not fall back -- it replaces whatever message
    was being written with a validation error about JWT_SECRET, or kills the first
    run outright. The bundler renames the executable to the product's name, which
    makes its stem the right answer here anyway.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).stem
    return "the app"


def _chosen_location(root: Path) -> Path | None:
    """The folder this copy was told to keep its records in, if it was told one."""
    pointer = root / POINTER
    if not pointer.is_file():
        return None
    text = pointer.read_text(encoding="utf-8").strip()
    return Path(text) if text else None


def resolve_data_dir() -> Path:
    """Where this copy's records live — asking on first run, refusing to guess after.

    THE FAILURE THIS IS BUILT AROUND
    Records may sit on an external disk. When that disk is not plugged in, the
    obvious behaviour -- fall back to the default folder and carry on -- creates an
    empty database and presents it as the app. To the owner that is indistinguishable
    from every record they have ever kept being deleted. So a location that was
    chosen and is now unreachable is a hard stop with the path named, never a
    silent fresh start.
    """
    root = install_dir()
    chosen = _chosen_location(root)

    if chosen is not None:
        drive = chosen.anchor or str(chosen)
        if not os.path.isdir(drive):
            raise SystemExit(
                f"\n  Your records are kept on {drive} which is not connected.\n\n"
                f"    {chosen}\n\n"
                f"  Plug that drive in and start {early_brand()} again.\n"
                f"  (If you meant to start over here instead, delete the file\n"
                f"   {root / POINTER} — your old records will not be touched.)\n")
        try:
            chosen.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SystemExit(f"\n  Cannot use {chosen}: {exc}\n")
        return chosen

    default = root / "data"

    # A .app carries its licence read-only inside itself; copy it out the first
    # time, before anything asks whether this copy has been used. Without this the
    # app starts with no licence and refuses to run, which looks like a faulty
    # download rather than a first run.
    seed = seed_dir()
    if seed is not None and not (default / "instance.env").exists():
        default.mkdir(parents=True, exist_ok=True)
        _carry_shipped_data(seed, default)

    # "Has this copy been used yet?" is instance.env, NOT finmate.db.
    #
    # A licensed bundle SHIPS data/finmate.db already made -- an empty database
    # carrying the branding -- so testing for the database meant the question was
    # never asked on the one kind of copy it was written for. instance.env holds
    # the per-installation secrets and is generated on the first real run, so it
    # is the marker that actually distinguishes "fresh out of the zip" from "in
    # use".
    if not (default / "instance.env").exists():
        try:
            # The window opens before the database exists, so the name it shows
            # comes from the executable rather than the branding row.
            os.environ.setdefault("APP_BRAND", early_brand())
            import wizard
            picked = wizard.ask_location(str(default))
        except Exception:
            picked = None
        if picked and Path(picked).resolve() != default.resolve():
            target = Path(picked)
            target.mkdir(parents=True, exist_ok=True)
            _carry_shipped_data(default, target)
            (root / POINTER).write_text(str(target) + "\n", encoding="utf-8")
            return target
    default.mkdir(parents=True, exist_ok=True)
    return default


def _carry_shipped_data(src: Path, dst: Path) -> None:
    """Take what the bundle shipped over to the folder the owner chose.

    The licensed bundle's data folder is not empty: it holds the signed licence,
    the vault key generated for this copy alone, and a database carrying the app's
    name and icon. Pointing somewhere else without bringing those produces a copy
    that starts with no licence -- so it refuses to run -- and a different vault
    key, which would make saved passwords unreadable. Both look like the choice of
    folder having broken the app.

    Copies rather than moves, and never overwrites: if anything here fails the
    originals are still where they were, and the pointer is written only after
    this returns.
    """
    import shutil
    if not src.is_dir():
        return
    for item in src.iterdir():
        target = dst / item.name
        if target.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        except OSError as exc:
            raise SystemExit(
                f"\n  Could not put your records in {dst}:\n    {exc}\n\n"
                f"  Nothing has been changed — start {early_brand()} again and\n"
                f"  choose a different folder.\n")


def prepare_environment() -> Path:
    """Point the app at a writable data folder and make sure it has its secrets."""
    import secrets as pysecrets

    data = resolve_data_dir()

    # The backend reads configuration through pydantic-settings, which takes
    # environment variables ahead of any .env file. Setting them here means the
    # packaged app needs no .env at all for the values that are always the same.
    os.environ.setdefault("DB_ENGINE", "sqlite")
    os.environ.setdefault("DB_FILE", str(data / "finmate.db"))
    os.environ.setdefault("MEDIA_ROOT", str(data / "media"))
    os.environ.setdefault("LICENSE_FILE", str(data / "licence.key"))

    # Per-installation secrets, generated once and kept beside the data. Baking
    # these into the build would give every customer the same JWT signing key,
    # so a token minted on one copy would be accepted by every other.
    keyfile = data / "instance.env"
    values = {}
    if keyfile.exists():
        for line in keyfile.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    changed = False
    for name, maker in (("JWT_SECRET", lambda: pysecrets.token_urlsafe(48)),
                        ("MEDIA_SECRET", lambda: pysecrets.token_urlsafe(48)),
                        ("VAULT_KEY_HEX", lambda: pysecrets.token_hex(32))):
        if not values.get(name):
            values[name] = maker()
            changed = True
    if changed:
        keyfile.write_text(
            "# Generated for this installation. Losing VAULT_KEY_HEX makes every\n"
            "# saved password permanently unreadable — back this file up.\n"
            + "\n".join(f"{k}={v}" for k, v in values.items()) + "\n",
            encoding="utf-8")
    # A licensed build ships carried-secrets.env holding the vault key chosen for
    # it; that one wins, because the database was sealed with it.
    carried = data / "carried-secrets.env"
    if carried.exists():
        for line in carried.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    for k, v in values.items():
        os.environ.setdefault(k, v)

    licence = data / "licence.json"
    if licence.exists():
        import json
        try:
            info = json.loads(licence.read_text(encoding="utf-8"))
            os.environ.setdefault("LICENSED_MODE", "true")
            os.environ.setdefault("LICENSE_PUBLIC_KEY_HEX", info.get("public_key", ""))
            if info.get("check_url"):
                # LICENSE_CHECK_URL, never PUBLIC_BASE_URL. This is the address of
                # the PUBLISHER's server, and putting it in PUBLIC_BASE_URL made a
                # customer's own copy report the publisher's private domain as
                # "your web address" -- on their Profile screen, on someone else's
                # computer. The customer's address is theirs to set and starts
                # empty. (setup.py, the source-bundle path, always did this right.)
                os.environ.setdefault("LICENSE_CHECK_URL", info["check_url"])
        except ValueError:
            pass
    return data


def ensure_account(data: Path) -> None:
    """First run: create the one account this copy signs in with."""
    from app.database import Base, SessionLocal, engine
    from app.models import User

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count():
            return
    finally:
        db.close()

    import json
    import base64
    from create_account import create

    name, email, role = "", "", "user"
    token_file = data / "licence.key"
    if token_file.exists():
        try:
            body = token_file.read_text(encoding="utf-8").strip().split(".")[1]
            payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            name, email = payload.get("name", ""), payload.get("email", "")
        except Exception:
            pass

    # A window first. Someone who was handed this app should never have to meet a
    # console, and a shortcut-launched copy has no console to meet anyway.
    try:
        # wizard.py is bytecode inside the executable, so the build-time rename the
        # source bundle relies on cannot reach it. Handing it the name and colour
        # through the environment is what stops a renamed app asking for its
        # password under the name it was compiled with.
        try:
            from app.routers.branding import current as _branding
            from app.database import SessionLocal
            _db = SessionLocal()
            try:
                _b = _branding(_db)
                os.environ["APP_BRAND"] = _b["app_name"]
                os.environ["APP_THEME"] = _b["theme_color"]
            finally:
                _db.close()
        except Exception:
            pass        # the window is worth opening even unbranded
        import wizard
        picked = wizard.ask_account({"name": name, "email": email} if email else None)
    except Exception:
        picked = None
    if picked:
        create(picked["name"], picked["email"], picked["password"], picked["role"])
        print(f"\n  Account created — sign in as {picked['email']}\n")
        return

    if not sys.stdin or not sys.stdin.isatty():
        # No window and no console: nothing left to ask with.
        raise RuntimeError(
            f"{brand()} needs to set up its first account, which has to be done "
            f"from a console window. Run {brand()} from Terminal or Command Prompt "
            "once, then start it however you like afterwards.")

    print("\n  First run — let's create your sign-in.\n")
    if email:
        print(f"  This copy is licensed to {name} ({email}).")
    else:
        name = input("  Your name: ").strip() or "Owner"
        email = input("  Email address (your username): ").strip()
        role = "admin"          # an unlicensed copy is somebody's own installation
    while True:
        import getpass
        pw = getpass.getpass("  Choose a password (12+ characters): ")
        if len(pw) < 12:
            print("     Use at least 12 characters.")
            continue
        if getpass.getpass("  Type it again: ") != pw:
            print("     They don't match.")
            continue
        break
    create(name, email, pw, role)
    print(f"\n  Account created — sign in as {email}\n")


def brand() -> str:
    """What this copy calls itself, for the console a customer actually reads.

    The name is a row in the customer's own database, so this only works once
    prepare_environment() has pointed the app at it -- every caller here runs
    after that. Falls back rather than failing: a banner is not worth a crash.
    """
    try:
        from app.routers.branding import app_name
        return app_name()
    except Exception:
        # The data-location window opens before there is a database to read, so
        # fall back to the executable's own name -- the bundler renames it to the
        # product's name for each customer, which makes it the right answer here.
        if getattr(sys, "frozen", False):
            return Path(sys.executable).stem
        return "the app"


def _register_dll_dirs(base: Path) -> None:
    """Tell Windows where this build's native libraries live.

    onnxruntime_pybind11_state.pyd sits in _internal/onnxruntime/capi/ and needs
    onnxruntime.dll beside it plus the Microsoft C++ runtime, which PyInstaller
    puts in _internal/ itself. Python 3.8 stopped using PATH to resolve an
    extension module's dependencies, so a folder that is not registered simply is
    not searched -- and the failure reads as
    "DLL load failed ... A dynamic link library (DLL) initialization routine
    failed", which sounds like a corrupt file rather than a missing directory.

    Seen on a customer's Windows 10 machine and not on the build machine, which is
    what a search-path difference looks like from the outside.
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    for folder in (base, base / "onnxruntime" / "capi"):
        try:
            if folder.is_dir():
                os.add_dll_directory(str(folder))
        except OSError:
            pass


def _publish_version() -> None:
    """Tell the app which version it is, from the file the build stamped.

    Beside the executable rather than inside it, because the bundler renames the
    executable per customer and a plain file survives that untouched. Without this
    a copy reports "0", treats every release as newer, and offers an update it has
    already installed -- forever.
    """
    for candidate in (install_dir() / "version.txt", bundle_dir() / "version.txt"):
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                if value:
                    os.environ.setdefault("APP_VERSION", value)
                    return
        except OSError:
            pass


def main() -> int:
    base = bundle_dir()
    _register_dll_dirs(base)
    _publish_version()
    # The backend imports `app.*` and reads frontend/dist relative to itself.
    sys.path.insert(0, str(base / "backend"))
    os.chdir(str(base / "backend"))
    # Told, not inferred: inside the package app/ is frozen into the executable,
    # so the backend cannot find the web files by walking up from its own path.
    os.environ["FRONTEND_DIST"] = str(base / "backend" / "frontend" / "dist")

    data = prepare_environment()
    print("=" * 66)
    print(f"  {brand()}")
    print("=" * 66)
    print(f"  Your data: {data}")

    try:
        ensure_account(data)
    except Exception as exc:
        print(f"\n  {exc}\n")
        pause()
        return 1

    port = free_port(int(os.environ.get("PORT", "8080")))
    # Publish the port that was actually chosen. free_port() moves on when 8080 is
    # taken, and without this the app's own "open this on your phone" address kept
    # naming 8080 — a link that goes nowhere, which is worse than none at all.
    os.environ["PORT"] = str(port)
    local = f"http://127.0.0.1:{port}"

    # Ask Windows to let phones on the same Wi-Fi through, once, on first run.
    # Without this the phone address below is printed and simply does not answer:
    # uvicorn listens on every interface, but the firewall drops the connection
    # before it arrives, and the usual first-run prompt never appears for a program
    # started from a shortcut or at login.
    lan_ok = True
    try:
        from app import lanaccess
        marker = data / ".lan-asked"
        first_time = not marker.exists()
        if first_time and not lanaccess.exists():
            # Said BEFORE the box appears, not after. An unexplained permission
            # prompt on the first launch of software somebody was handed reads as
            # malware; the same prompt, one line after being told it is coming and
            # what it is for, reads as the app doing what it said it would.
            print("\n  Windows needs permission before your phone can reach this\n"
                  "  computer. A permission box is about to appear:\n"
                  "    Yes  - use it from your phone on this Wi-Fi\n"
                  "    No   - keep it to this computer only\n"
                  "  Either way the app works. You can change it later in Profile.\n")
        lanaccess.ensure(elevate=first_time)
        marker.write_text("asked\n", encoding="utf-8")   # ask once, not every launch
        # Both conditions: a rule on a network Windows calls Public does nothing,
        # and that combination looks identical to having no rule at all.
        lan_ok = lanaccess.reachable()
    except Exception:
        pass

    print(f"\n  On this computer : {local}")
    print(f"  On your phone    : http://{lan_address()}:{port}"
          + ("" if lan_ok else "   (blocked by the firewall — see below)"))
    if not lan_ok:
        try:
            from app import lanaccess
            print("\n" + lanaccess.advice(port))
        except Exception:
            pass
    print(f"\n  Leave this window open. Press Ctrl+C to stop {brand()}.\n")

    threading.Thread(target=lambda: (time.sleep(2), webbrowser.open(local)),
                     daemon=True).start()

    import uvicorn
    from app.main import app
    # server_header=False: do not announce the server software in every
    # response. uvicorn adds it after the app middleware, so this is the
    # only place it can be turned off.
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning",
                access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
