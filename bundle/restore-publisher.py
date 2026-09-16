"""Stand this publisher installation up on a new machine.

WHY THIS EXISTS. Moving the publisher box by hand took an afternoon and went
wrong in ways that all looked like something else: MySQL binaries that never
arrived, a venv holding absolute paths from the old drive, the wrong Python
version, and a database that reported healthy while holding zero tables. None of
those announce themselves -- the app boots, /api/health says ok, and sign-in
fails with no clue why.

So this does the same steps in a fixed order, checks each one, and says what it
found rather than assuming. It installs nothing without asking.

    python restore-publisher.py              # walk through it
    python restore-publisher.py --check      # report only, change nothing

It deliberately does NOT need MySQL. The database in the bundle is SQLite, which
needs no server and is a supported engine for this app -- that is what makes the
move portable. Point DB_ENGINE at mysql afterwards if you want one.
"""
import argparse
import base64
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BACKEND = HERE / "backend"

# The version this app is built and tested against. A mismatch is not fatal --
# it is reported, because "it ran on 3.11 and crashed oddly later" is worse than
# being told up front.
WANT_PY = (3, 13)


def say(msg=""):
    print(msg, flush=True)


def ok(label, good, detail=""):
    say(f"  [{'OK  ' if good else 'FAIL'}] {label}{('  -- ' + detail) if detail else ''}")
    return good


def ask(question, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        reply = input(f"  {question} {suffix} ").strip().lower()
    except EOFError:
        return default
    if not reply:
        return default
    return reply.startswith("y")


def check() -> dict:
    """What is here, and what this machine can do about it."""
    say("Checking what arrived")
    found = {}

    found["backend"] = ok("backend source", (BACKEND / "app" / "main.py").is_file())
    found["frontend_src"] = ok("frontend source (UI can be changed)",
                               (HERE / "frontend" / "src").is_dir())
    found["frontend_dist"] = ok("built web app", (HERE / "frontend" / "dist" / "index.html").is_file())
    found["packaging"] = ok("packaging/ (customer builds can be made)",
                            (HERE / "packaging").is_dir())
    found["database"] = ok("database", (DATA / "finmate.db").is_file())

    media = DATA / "media"
    n = sum(1 for _ in media.rglob("*")) if media.is_dir() else 0
    found["media"] = ok("photos and documents", media.is_dir(), f"{n} entries")

    found["vault"] = ok("vault key (saved passwords readable)",
                        (DATA / "carried-secrets.env").is_file())
    found["secrets"] = ok("publisher keys (licences can be issued)",
                          (DATA / "publisher-secrets.enc").is_file())
    found["tunnel"] = ok("tunnel credentials", (DATA / "cloudflared").is_dir())

    say()
    say("Checking this machine")
    v = sys.version_info
    ok(f"Python {v.major}.{v.minor}.{v.micro}", v[:2] == WANT_PY,
       "" if v[:2] == WANT_PY else f"this app is built against {WANT_PY[0]}.{WANT_PY[1]}")
    found["node"] = ok("node (only needed to rebuild the UI)", bool(shutil.which("node")))
    found["cloudflared"] = ok("cloudflared (only needed for the public address)",
                              bool(shutil.which("cloudflared")) or
                              Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe").exists())

    origin = HERE / "SOURCE-ORIGIN.json"
    if origin.is_file():
        info = json.loads(origin.read_text(encoding="utf-8"))
        say()
        say(f"  This tree came from {info.get('remote') or '(no remote)'}")
        say(f"  commit {info.get('commit', '')[:12]} on {info.get('branch') or '?'}"
            + ("  (had uncommitted changes)" if info.get("dirty") else ""))
    return found


def decrypt_secrets(path: Path, passphrase: str) -> str:
    """Open publisher-secrets.enc WITHOUT importing the app.

    This deliberately re-implements the six lines bundler.decrypt_publisher_secrets
    contains rather than importing it. Importing anything under `app` pulls in
    app.config, which REQUIRES jwt_secret, vault_key_hex and media_secret — the
    very values still locked inside this file. config.py then raises SystemExit
    and the restore dies with a validation error telling you to write the .env
    this script exists to write.

    It is the trap CLAUDE.md #11 already names: nothing before the environment is
    prepared may import `app`. Found by running a real restore, which is the only
    way it shows up.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    key = Scrypt(salt=base64.b64decode(blob["salt"]), length=32,
                 n=blob.get("n", 2 ** 15), r=blob.get("r", 8),
                 p=blob.get("p", 1)).derive(passphrase.encode())
    return AESGCM(key).decrypt(base64.b64decode(blob["nonce"]),
                               base64.b64decode(blob["ct"]), None).decode("utf-8")


def reexec_in_venv() -> None:
    """Continue under the venv's interpreter when this one cannot decrypt.

    `cryptography` lives in the venv, and the venv is created by this script — so
    the interpreter that started it often cannot open the secrets file. Rather
    than ask the user to run it twice, hand over once the venv exists.
    """
    py = BACKEND / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.is_file() or os.environ.get("SAFENEST_RESTORE_REEXEC"):
        return
    try:
        import cryptography  # noqa: F401
        return                      # this interpreter is fine
    except ImportError:
        pass
    say()
    say("  Continuing under the environment just created...")
    os.environ["SAFENEST_RESTORE_REEXEC"] = "1"
    os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])


def write_env(found: dict) -> bool:
    """Reassemble backend/.env from what travelled.

    The vault key and the publisher keys arrive in two different files for a
    reason -- one prevents data loss and may sit in the clear, the other is the
    ability to issue licences and may not -- but .env wants them together.
    """
    env_path = BACKEND / ".env"
    if env_path.exists():
        say()
        say(f"  {env_path} already exists.")
        if not ask("Overwrite it?", default=False):
            say("  Left it alone. Nothing else here depends on writing it.")
            return False

    # ABSOLUTE paths, and this is not tidiness.
    #
    # config.sqlite_path resolves a relative DB_FILE against BACKEND_DIR, so
    # "data/finmate.db" means <bundle>/backend/data/finmate.db -- while the
    # database that travelled is at <bundle>/data/finmate.db. The app then finds
    # no file, creates an empty one, starts perfectly, answers /api/health, and
    # refuses every sign-in. Branding reads "App" instead of the real name and
    # nothing anywhere says why.
    #
    # Caught by booting a restored copy and trying to log in. Static inspection
    # of the folder said everything had arrived, and it had -- just not where the
    # app looks. MEDIA_ROOT has exactly the same shape of problem.
    db_file = (DATA / "finmate.db").resolve()
    media_root = (DATA / "media").resolve()
    lines = ["# Rebuilt by restore-publisher.py on this machine.",
             "# DB_ENGINE is sqlite because that is what travelled -- no server needed.",
             "# Absolute: a relative DB_FILE resolves against backend/, not this folder.",
             "DB_ENGINE=sqlite",
             f"DB_FILE={db_file}",
             f"MEDIA_ROOT={media_root}"]

    carried = DATA / "carried-secrets.env"
    if carried.is_file():
        for line in carried.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                lines.append(line.strip())

    if found.get("secrets"):
        say()
        say("  The publisher keys are encrypted. Without them this machine can run")
        say("  the app but cannot issue a single licence.")
        # getpass reads the TERMINAL, not stdin, so it cannot be piped — which
        # makes an unattended restore impossible and is not obvious until you try
        # one. The variable is read once and never echoed.
        from_env = os.environ.get("SAFENEST_RESTORE_PASSPHRASE", "")
        for attempt in range(3):
            phrase = from_env or getpass.getpass("  Passphrase (blank to skip): ")
            from_env = ""      # one shot: a wrong one must not loop forever
            if not phrase:
                say("  Skipped. You can re-run this script later to add them.")
                break
            try:
                text = decrypt_secrets(DATA / "publisher-secrets.enc", phrase)
            except ImportError:
                say("  The 'cryptography' package is not available to this Python.")
                say("  Let this script create the environment first, then re-run it.")
                break
            except Exception:
                say(f"  That passphrase did not work. {2 - attempt} attempt(s) left.")
                continue
            # These win over anything carried above: same key, better source.
            carried_keys = {l.split("=", 1)[0] for l in text.splitlines() if "=" in l}
            lines = [l for l in lines
                     if not ("=" in l and l.split("=", 1)[0] in carried_keys)]
            lines += [l.strip() for l in text.splitlines() if "=" in l]
            say("  Publisher keys restored -- this machine can issue licences.")
            break

    # DB_ENGINE may have arrived from the old machine saying "mysql". The database
    # in this bundle is SQLite, so honouring that would point the app at a server
    # that is not here and fail in a way that reads as data loss.
    # The old machine's DB_* travelled inside the encrypted file and may say
    # "mysql" against a server that does not exist here. Drop those and restate
    # what is actually true of this folder.
    lines = [l for l in lines if not l.startswith(("DB_ENGINE=", "DB_FILE=", "DB_HOST=",
                                                   "DB_PORT=", "DB_NAME=", "DB_USER=",
                                                   "DB_PASSWORD=", "MEDIA_ROOT="))]
    lines[1:1] = ["DB_ENGINE=sqlite", f"DB_FILE={db_file}", f"MEDIA_ROOT={media_root}"]

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"  Wrote {env_path}")
    return True


def find_python() -> str | None:
    """An interpreter of the version this app is actually built for.

    Building the environment with whatever happened to run this script is how you
    get a 3.11 venv for an app compiled against 3.13: it installs, it starts, and
    it fails later in ways that point nowhere near the cause. The check above
    already prints the mismatch -- this is what stops it being merely a warning
    somebody scrolls past.
    """
    if sys.version_info[:2] == WANT_PY:
        return sys.executable
    want = f"{WANT_PY[0]}.{WANT_PY[1]}"
    # The Windows launcher knows about every installed version; `python3.13` is
    # the usual name elsewhere.
    for probe in ([["py", f"-{want}", "-c", "import sys; print(sys.executable)"]]
                  if os.name == "nt" else
                  [[f"python{want}", "-c", "import sys; print(sys.executable)"]]):
        try:
            out = subprocess.run(probe, capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return None


def make_venv() -> bool:
    venv = BACKEND / "venv"
    if venv.exists():
        say(f"  {venv} already exists -- leaving it. Delete it first to rebuild.")
        return True

    py = find_python()
    if py is None:
        want = f"{WANT_PY[0]}.{WANT_PY[1]}"
        say(f"  This app is built for Python {want} and no {want} was found here.")
        say(f"  Install it (python.org, or `winget install Python.Python.{want}`)")
        say( "  and run this again. Building the environment with a different")
        say( "  version installs cleanly and breaks later, which is worse.")
        return False
    if py != sys.executable:
        say(f"  Using {py}")
    if not ask("Create the Python environment and install dependencies? (a few minutes)"):
        return False
    say("  Creating venv...")
    subprocess.run([py, "-m", "venv", str(venv)], check=True)
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    say("  Installing dependencies...")
    r = subprocess.run([str(py), "-m", "pip", "install", "-q", "-r",
                        str(BACKEND / "requirements.txt")])
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    args = ap.parse_args()

    say()
    say("Restoring a publisher installation")
    say("=" * 34)
    say()
    found = check()

    if args.check:
        say()
        say("  --check: nothing was changed.")
        return 0

    say()
    if not ask("Go ahead and set this machine up?"):
        say("  Nothing changed.")
        return 0

    # Order matters: the environment first, because writing .env needs to decrypt
    # the secrets and the library that does it lives in that environment.
    make_venv()
    reexec_in_venv()
    say()
    write_env(found)

    py = BACKEND / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    say()
    say("Next")
    say(f"  1. cd {BACKEND}")
    say(f"     {py} -m uvicorn app.main:app --host 127.0.0.1 --port 8080 \\")
    say( "         --no-access-log --no-server-header")
    say( "  2. Sign in, then check Profile -> Licences offers the issue button.")
    say( "     If it does not, the signing key did not arrive -- re-run this script")
    say( "     and give the passphrase.")
    if found.get("tunnel"):
        say( "  3. Copy data/cloudflared/*.json into ~/.cloudflared/ and run")
        say( "     'Install App Services.bat' as administrator for the public address.")
    say()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
