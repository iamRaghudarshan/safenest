"""Build a portable App bundle.

Produces a folder that can be copied to a USB drive and run on another computer.
The same code backs both entry points — the "Move to another computer" button in
the app and the make_bundle.py command line — so they can never drift apart.

The bundle is deliberately plain files rather than a compiled installer: a folder
copies onto a pendrive, survives being moved around, and can be inspected. What
makes it work on a fresh machine is setup.py, which installs the dependencies and
asks the questions.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

from . import ist
from .config import BACKEND_DIR

PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND = PROJECT_ROOT / "frontend"
BUNDLE_SRC = PROJECT_ROOT / "bundle"

WINDOWS, MAC = "windows", "mac"

# The launcher scripts on disk keep these fixed names — they are templates. What
# gets written into a bundle is named after the app instead, so a customer opening
# the folder sees their software's name and not the one it was first written under.
TEMPLATES = {
    WINDOWS: "Start App (Windows).bat",
    MAC: "Start App (Mac).command",
}
# The literal replaced throughout the bundled text files at build time. Capitalised
# on purpose: every functional string in setup.py and the launchers — finmate.db,
# finmate-config.json, the MySQL user — is lower case, so a case-sensitive swap
# renames all the wording and touches none of the plumbing.
BRAND_TOKEN = "App"
# Files whose text is rebranded on the way into the bundle. Binary files and the
# application's own source are left alone.
REBRAND_FILES = {"setup.py", "wizard.py", "README.txt",
                 TEMPLATES[WINDOWS], TEMPLATES[MAC]}


def _display_name(name: str) -> str:
    """The app name reduced to characters that are safe in a filename.

    A name is typed by a person into a text box and then becomes a file on disk
    and a line inside a .bat script, so the characters that would break either —
    path separators, wildcards, and the batch escape characters — have to go.
    """
    cleaned = re.sub(r"[<>:\"/\\|?*%&^!]", "", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:40] or BRAND_TOKEN


def _slug(name: str) -> str:
    """Filename-safe form with no spaces, for folder names."""
    return re.sub(r"\s+", "-", _display_name(name)) or BRAND_TOKEN


def platform_layout(app_name: str) -> dict:
    """Folder and launcher names for this build, from the app's current name."""
    disp, slug = _display_name(app_name), _slug(app_name)
    return {
        WINDOWS: {"folder": f"{slug}-for-Windows", "launcher": f"Start {disp} (Windows).bat"},
        MAC: {"folder": f"{slug}-for-Mac", "launcher": f"Start {disp} (Mac).command"},
    }


def current_app_name() -> str:
    """What this installation calls itself, or the default if it never renamed."""
    try:
        from .database import SessionLocal
        from .models import Branding
        db = SessionLocal()
        try:
            row = db.query(Branding).filter(Branding.id == 1).first()
            return (row.app_name if row and row.app_name else BRAND_TOKEN)
        finally:
            db.close()
    except Exception:
        return BRAND_TOKEN

# Never copied: build artefacts, caches, local secrets, and the live media tree
# (which is exported separately and only when the user asks for it).
SKIP_DIRS = {"venv", ".venv", "__pycache__", "private", "uploads", "data", "node_modules",
             ".git", "certs", ".pytest_cache", ".mypy_cache", "bundle"}
SKIP_FILES = {".env", ".env.local", "install-services.log"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm", ".part"}
# models/ is NOT skipped: ~190 MB of face and CLIP weights ride along so the
# copy works offline on arrival instead of needing its own download.


def default_output_root() -> Path:
    """Somewhere the user will actually find it. Desktop when there is one."""
    home = Path.home()
    for candidate in (home / "Desktop", home / "OneDrive" / "Desktop"):
        if candidate.is_dir():
            return candidate
    return PROJECT_ROOT.parent


def _copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for item in sorted(src.iterdir()):
        if item.is_dir():
            if item.name in SKIP_DIRS:
                continue
            count += _copy_tree(item, dst / item.name)
        elif item.name not in SKIP_FILES and item.suffix.lower() not in SKIP_SUFFIXES:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / item.name)
            count += 1
    return count


def _read_env_value(key: str) -> str:
    env = BACKEND_DIR / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def _write_launchers(dest: Path, platform: str, app_name: str):
    """Put the chosen platform's launcher at the top level and the other one in a
    subfolder, so picking the wrong platform is a detour rather than a dead end.
    Line endings matter: .bat needs CRLF, and a shell script must not have any.

    Each file is read from its fixed template name and written out under the app's
    own name, with the wording rebranded on the way through.
    """
    other = MAC if platform == WINDOWS else WINDOWS
    spare = dest / "for-the-other-platform"
    spare.mkdir(exist_ok=True)
    layout = platform_layout(app_name)

    for plat, target_dir in ((platform, dest), (other, spare)):
        src = BUNDLE_SRC / TEMPLATES[plat]
        if not src.exists():
            continue
        name = layout[plat]["launcher"]
        text = src.read_text(encoding="utf-8").replace(BRAND_TOKEN, _display_name(app_name))
        newline = "\r\n" if name.endswith(".bat") else "\n"
        out = target_dir / name
        out.write_text(text, encoding="utf-8", newline=newline)
        if name.endswith(".command"):
            os.chmod(out, 0o755)  # Finder refuses to run a non-executable script

    (spare / "READ ME.txt").write_text(
        "This folder holds the launcher for the other operating system.\n"
        "If you copied this bundle to a different kind of computer than you first\n"
        "chose, move the launcher from here into the main folder and use it instead.\n"
        "Everything else in the bundle works on both.\n", encoding="utf-8")


def _zip_bundle(folder: Path, target: Path, executables: set[str]):
    """Zip the bundle, recording Unix permissions.

    This is what makes a Mac bundle usable. Windows has no executable bit to set,
    and a USB stick formatted FAT/exFAT wouldn't carry one anyway — so Finder would
    refuse to run the launcher. A zip stores the mode itself, and macOS restores it
    on extract, so double-clicking the .command works on arrival.
    """
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            arc = path.relative_to(folder.parent).as_posix()
            info = zipfile.ZipInfo(arc, time.localtime(path.stat().st_mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix — otherwise the mode bits are ignored
            mode = 0o755 if path.name in executables else 0o644
            info.external_attr = mode << 16
            with path.open("rb") as src, z.open(info, "w") as dst:
                shutil.copyfileobj(src, dst, 1024 * 256)


def _export_database(target: Path) -> bool:
    result = subprocess.run([sys.executable, "export_db.py", str(target)],
                            cwd=str(BACKEND_DIR), capture_output=True, text=True)
    return result.returncode == 0 and target.exists()


def new_vapid_keys() -> tuple[str, str]:
    """A fresh push-notification keypair for a copy that is going somewhere else.

    Push identifies the *server* to Apple and Google by this key, so the publisher's
    pair cannot be handed out — and simply omitting it, which is what happened
    before, leaves the customer with "push notifications are not configured on the
    server" and no way to fix it. Every independent copy needs its own.

    Raw P-256, base64url, unpadded: 65 bytes uncompressed point for the public half
    and the 32-byte scalar for the private one, which is the shape pywebpush wants.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def b64(raw: bytes) -> str:
        import base64
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return b64(public), b64(private)


def _write_carried_secrets(data_dir: Path, vault_key: str | None = None):
    """Carry only the secrets whose loss would destroy data.

    The vault key is the one that truly matters: saved passwords are encrypted with
    it and are unreadable without it. JWT and media secrets are deliberately left
    behind — regenerating them just means signing in again on the new machine.

    A personal export passes its own freshly-generated `vault_key`, so this server's
    shared key (which protects every other user's secrets) never leaves the machine.
    """
    lines = ["# Secrets that travel with your data. Keep this file private.",
             "# VAULT_KEY_HEX decrypts your saved passwords — without it they are lost."]
    if vault_key:
        lines.append(f"VAULT_KEY_HEX={vault_key}")
        # Its own push identity, generated here. Without this the copy starts up
        # with push permanently unavailable and nothing the owner can do about it.
        pub, priv = new_vapid_keys()
        lines += ["# Push notification identity, generated for this copy alone.",
                  f"VAPID_PUBLIC_KEY={pub}", f"VAPID_PRIVATE_KEY={priv}"]
    else:
        lines.append(f"VAULT_KEY_HEX={_read_env_value('VAULT_KEY_HEX')}")
        legacy = _read_env_value("VAULT_KEY_LEGACY_HEX")
        if legacy:
            # Anything not yet moved onto the current key still needs the old one.
            lines.append(f"VAULT_KEY_LEGACY_HEX={legacy}")
        pub, priv = _read_env_value("VAPID_PUBLIC_KEY"), _read_env_value("VAPID_PRIVATE_KEY")
        if pub and priv:
            lines += [f"VAPID_PUBLIC_KEY={pub}", f"VAPID_PRIVATE_KEY={priv}"]
    (data_dir / "carried-secrets.env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_database(target: Path) -> bool:
    """A database with the schema and nothing in it.

    What a licensed copy must NOT contain, and did until this existed: every one
    of your users and their password hashes, your expenses, your photos, your
    audit log, and the licences table naming every other customer you have sold
    to. Handing that over with the software would be a data breach dressed up as
    a feature. A customer's copy starts empty; their own account is created on
    first run from the name on the licence.
    """
    from sqlalchemy import create_engine
    from .database import Base
    from . import models  # noqa: F401  — registers every table on Base.metadata

    target.parent.mkdir(parents=True, exist_ok=True)
    for path in (target, target.with_name(target.name + "-wal"),
                 target.with_name(target.name + "-shm")):
        if path.exists():
            path.unlink()
    engine = create_engine(f"sqlite:///{target.as_posix()}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    return target.exists()


def _carry_branding(db_path: Path, media_dir: Path) -> bool:
    """Give the copy the app's name and icon.

    Needed because the licensed build ships a deliberately empty database, and the
    app's name now lives in that database. Without this a customer would install
    a copy still calling itself whatever the software was named when it was first
    written, however many times it has been renamed since.

    Only the name, colour and icon travel. This is the publisher's branding, not
    anyone's records, so it is safe to carry into a build that must contain no
    personal data at all — and it is copied field by field rather than by cloning
    a row, so nothing else can come along by accident.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from .database import SessionLocal
    from .models import Branding
    from .routers.branding import BRAND_DIR, SIZES

    src = SessionLocal()
    try:
        row = src.query(Branding).filter(Branding.id == 1).first()
        if not row:
            return False                      # never renamed; the build's default stands
        fields = {
            "app_name": row.app_name, "short_name": row.short_name,
            "tagline": row.tagline, "theme_color": row.theme_color,
            "icon_version": int(row.icon_version or 0),
        }
    finally:
        src.close()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        out = sessionmaker(bind=engine)()
        out.add(Branding(id=1, updated_at=ist.now(), **fields))
        out.commit()
        out.close()
    finally:
        engine.dispose()

    # The rendered icons live beside the media, so the copy serves them from its
    # own data folder exactly as this installation does.
    if fields["icon_version"]:
        dest = media_dir / "branding"
        dest.mkdir(parents=True, exist_ok=True)
        for size in SIZES:
            srcfile = Path(BRAND_DIR) / f"icon-{size}.png"
            if srcfile.is_file():
                shutil.copy2(srcfile, dest / srcfile.name)
    return True


def _carry_hosting(data_dir: Path, hostname: str, tunnel_token: str) -> None:
    """The customer's own web address, ready to use.

    A tunnel token rather than credential files: a remotely-managed tunnel gets
    its routing from Cloudflare, so this one string is the whole configuration and
    the address can be repointed — or switched off — without touching their
    machine. Setup starts cloudflared with it and asks them nothing.
    """
    (data_dir / "hosting.json").write_text(json.dumps({
        "hostname": hostname,
        "url": f"https://{hostname}",
        "tunnel_token": tunnel_token,
    }, indent=2), encoding="utf-8")


def _carry_licence(data_dir: Path, token: str) -> None:
    """Put the customer's licence, and only the public half of the key, into the build.

    The signing key must never travel. Anyone holding it can mint licences for
    themselves and for anyone else, which would make the whole scheme decorative.
    setup.py reads these and turns licensed mode on at the far end.
    """
    (data_dir / "licence.key").write_text(token.strip() + "\n", encoding="utf-8")
    (data_dir / "licence.json").write_text(json.dumps({
        "licensed": True,
        "public_key": _read_env_value("LICENSE_PUBLIC_KEY_HEX"),
        "check_url": _read_env_value("PUBLIC_BASE_URL"),
    }, indent=2), encoding="utf-8")


def _carry_tunnel(data_dir: Path) -> bool:
    """Take the Cloudflare tunnel along with the move.

    Without this the copy arrives able to serve everything except the one address
    people actually use it from, and the only way to fix it is hand-copying two
    files off the old machine — which assumes the old machine is still to hand.

    Whole-server moves only. A personal export must never carry these: they are
    the credentials for the tunnel itself, and handing them to an individual user
    would let them redirect everyone's address at a server of their own.
    """
    src = Path.home() / ".cloudflared"
    if not src.is_dir():
        return False
    creds = sorted(p for p in src.glob("*.json") if len(p.stem) == 36)  # <uuid>.json
    if not creds:
        return False

    out = data_dir / "cloudflared"
    out.mkdir(parents=True, exist_ok=True)
    for path in creds + [src / "cert.pem"]:
        if path.exists():
            shutil.copy2(path, out / path.name)
    (out / "tunnel.json").write_text(json.dumps({
        "tunnel_id": creds[0].stem,
        "public_url": _read_env_value("PUBLIC_BASE_URL"),
    }, indent=2), encoding="utf-8")
    return True


def _copy_media(dest: Path, progress, user_id: int | None = None) -> tuple[int, int]:
    """Copy the uploaded files. With a user_id, only that user's folders — the
    storage layout is private/<module>/<user_id>/<variant>/, so one user's files
    are already a set of directories rather than something to filter file by file."""
    from . import storage
    src = Path(storage.PRIVATE_ROOT)
    if not src.is_dir():
        return 0, 0

    if user_id is None:
        everything = [p for p in src.rglob("*") if p.is_file()]
    else:
        everything = []
        for module in (storage.GALLERY, storage.DOCUMENTS, storage.AVATARS):
            owned = src / module / str(int(user_id))
            if owned.is_dir():
                everything += [p for p in owned.rglob("*") if p.is_file()]
    total_files = len(everything) or 1
    files = size = 0
    for path in everything:
        out = dest / path.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        files += 1
        size += path.stat().st_size
        if files % 250 == 0:
            progress(f"Copying photos and documents ({files:,} of {total_files:,})",
                     45 + int(50 * files / total_files))
    return files, size


# The template folder name inside dist-app. Capitalised App on purpose: it is
# a filename the build produces, not a word anybody reads. See §9.
APP_DIR_NAME = "App"
COMPILED_DIR = PROJECT_ROOT / "dist-app" / APP_DIR_NAME


def host_platform() -> str:
    """The platform this machine can COMPILE for.

    PyInstaller freezes the interpreter it is run by, so a Windows machine can
    only produce Windows binaries. There is no cross-compiling and no flag that
    changes it.

    Note what this is not: the platforms this machine can ISSUE a copy for. Those
    are whichever compiled builds are present — see compiled_dir().
    """
    return MAC if sys.platform == "darwin" else WINDOWS


def compiled_dir(platform: str | None = None) -> Path:
    """Where the compiled build for a platform lives.

    Per-platform folders so ONE machine can issue copies for both. Compiling still
    has to happen on the matching system, but that only has to produce the binaries
    once per release — after which `dist-app/mac/` sits here beside
    `dist-app/App/` and licences for either are issued from this laptop, from
    this one copy of the source.

    `dist-app/App` stays the host platform's build so existing builds, scripts
    and bundles keep working untouched.
    """
    platform = platform or host_platform()
    per_platform = PROJECT_ROOT / "dist-app" / platform / APP_DIR_NAME
    if per_platform.is_dir():
        return per_platform
    if platform == host_platform():
        return COMPILED_DIR
    return per_platform          # reported as missing, with instructions


def compiled_available(platform: str | None = None) -> bool:
    """Is there a compiled build to hand a customer for this platform?

    Produced by `python packaging/build_exe.py --native`, which turns the app into
    machine code. Checked rather than assumed, because the alternative — quietly
    falling back to the source bundle — would hand a customer every line of the
    code without anyone noticing.
    """
    platform = platform or host_platform()
    root = compiled_dir(platform)
    # A Mac build has no .exe suffix; a Windows one does. The name is checked
    # against the TARGET platform, not this machine, or an imported Mac build
    # would be judged by Windows' rules and always look missing.
    exe = root / ("App.exe" if platform == WINDOWS else "App")
    return exe.is_file() and (root / "_internal").is_dir()


def ready_platforms() -> list:
    """Which platforms this machine can issue a customer copy for right now.

    A Mac build usually arrives here as the CI tarball and is never unpacked --
    Windows cannot represent the symlinks inside Python.framework, so `build()`
    copies entries straight out of the tar. Asking only `compiled_available()`
    therefore reported Mac as missing while `build_licensed()` would have built it
    perfectly well, and the Licences screen greyed out a button that worked. The
    screen has to agree with the builder; the builder is the authority.
    """
    return [p for p in sorted(TEMPLATES)
            if compiled_available(p) or (p == MAC and mac_tarball().is_file())]


def installed_root() -> Path | None:
    """The folder a packaged copy was installed into, or None when running source.

    `sys.frozen` is what PyInstaller sets; `sys.executable` is then the customer's
    own .exe rather than a Python interpreter, and its folder holds `_internal`,
    the licence and the data. Deliberately not `sys._MEIPASS`: in one-file mode
    that is a temporary directory that vanishes on exit.
    """
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent
    return root if (root / "_internal").is_dir() else None


def build_installed(platform: str, include_data: bool, out_root: Path | None = None,
                    progress=lambda step, pct: None, user_id: int | None = None,
                    folder_suffix: str = "", make_zip: bool = False) -> dict:
    """Export from inside a packaged copy, by cloning the installation itself.

    WHY THIS EXISTS
    `build()` assembles a bundle out of the source tree -- backend/, frontend/dist
    and bundle/setup.py. A customer's copy contains none of those; the code is
    machine code inside `_internal`. So export failed there with "frontend/dist is
    missing", in every licence state, which meant the one feature that gets a
    customer's records out of the app did not work for the people it was for.

    The customer already HAS a working installation, so the copy is made from that
    rather than rebuilt from sources they were never given. Their own licence
    travels with it -- this moves someone's app to their new machine, it does not
    mint an unlicensed one.
    """
    root = installed_root()
    if root is None:
        raise RuntimeError("Not a packaged copy — use build() instead.")
    if platform not in TEMPLATES:
        raise ValueError(f"platform must be one of {sorted(TEMPLATES)}")
    # The copy is made of the binaries on this machine, so it only runs on this
    # machine's platform. Producing a Mac-named folder full of Windows executables
    # would fail on the far end, long after the person had carried it there.
    here = MAC if sys.platform == "darwin" else WINDOWS
    if platform != here:
        raise ValueError(
            f"This copy can only export for {here.title()}, because it copies the "
            f"app installed here. To move to the other kind of computer, ask "
            f"whoever supplied {current_app_name()} for a copy built for it.")

    out_root = Path(out_root) if out_root else default_output_root()
    app_name = current_app_name()
    layout = platform_layout(app_name)
    dest = out_root / (layout[platform]["folder"] + folder_suffix)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)

    progress("Copying the app", 10)
    # Skip `data` (the new copy gets a freshly exported one) and never recurse into
    # an earlier export sitting in the same folder.
    def _skip(directory, names):
        if Path(directory).resolve() == root:
            return {n for n in names
                    if n == "data" or n.startswith(layout[platform]["folder"])}
        return set()

    shutil.copytree(root, dest, ignore=_skip)

    data_dir = dest / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    result = {"platform": platform, "folder": str(dest), "media_files": 0,
              "media_bytes": 0, "with_data": bool(include_data), "database": False,
              "scope": "mine" if user_id else "all", "from_installed": True,
              "app_name": app_name, "launcher": layout[platform]["launcher"]}

    if include_data:
        progress("Exporting your records", 45)
        if user_id:
            from . import userexport
            summary = userexport.export_for_user(data_dir / "finmate.db", user_id)
            result["database"] = True
            result["rows"] = summary["rows"]
            result["unreadable_vault_fields"] = summary["unreadable_vault_fields"]
            _write_carried_secrets(data_dir, vault_key=summary["vault_key"])
        else:
            result["database"] = _export_database(data_dir / "finmate.db")
            _write_carried_secrets(data_dir)
        progress("Copying photos and documents", 60)
        result["media_files"], result["media_bytes"] = _copy_media(
            data_dir / "media", progress, user_id=user_id)

    # The licence follows the customer to their new machine. Leaving it behind
    # would produce a copy that opens and then refuses to work, which reads as the
    # export having failed.
    licence_src = root / "data" / "licence.key"
    if licence_src.is_file():
        try:
            _carry_licence(data_dir, licence_src.read_text(encoding="utf-8").strip())
            result["licensed"] = True
        except Exception:
            result["licensed"] = False

    progress("Finishing", 92)
    result["bytes"] = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())

    if make_zip or platform == MAC:
        progress("Compressing", 96)
        zpath = dest.parent / f"{dest.name}-{ist.today():%Y-%m-%d}.zip"
        _zip_bundle(dest, zpath, executables={layout[MAC]["launcher"],
                                              _display_name(app_name)})
        result["zip"] = str(zpath)
        result["zip_bytes"] = zpath.stat().st_size

    progress("Done", 100)
    return result


def _mac_gatekeeper_note(brand: str, launcher: str, folder: str) -> list:
    """What a Mac owner must do before the app will open at all.

    macOS refuses anything downloaded that Apple has not signed, and the refusal
    reads as an accusation: "Apple could not verify this app is free of malware",
    with the only button being **Move to Bin**. A customer who has just paid for
    software and is told to bin it does not try again -- and nothing in the app
    can catch this, because macOS blocks it before a line of ours runs.

    The old right-click -> Open route stopped working in recent macOS, so both
    current routes are given. Removing the quarantine flag is the reliable one.

    The real fix is an Apple Developer account and notarisation; until then this
    page is the difference between a working copy and a refund.
    """
    return [
        "  INSTALLING IT",
        "",
        f"    1. Drag {brand}.app into your Applications folder.",
        "",
        f"    2. Open Applications, RIGHT-CLICK {brand} and choose Open.",
        "       (Right-click, not double-click, only this first time.)",
        "",
        "    3. macOS will say it cannot verify the developer. Choose Open.",
        "",
        "    That is all. From then on it opens with a normal double-click.",
        "",
        "  WHY macOS ASKS",
        "    Apple charges software makers a yearly fee to vouch for their apps.",
        "    This one is signed but not registered with Apple, so macOS asks you",
        "    once whether you trust it. Nothing is wrong with the download.",
        "",
        "    If you double-clicked and were only offered 'Move to Bin', that is",
        "    the same thing: right-click and choose Open instead, and the Open",
        "    button appears. Or go to System Settings > Privacy & Security and",
        f"    click 'Open Anyway' beside the note about {brand}.",
        "",
    ]


MAC_TARBALL = "mac-app.tar.gz"


def mac_tarball() -> Path:
    """The Mac build as CI packed it, symlinks and modes intact."""
    return PROJECT_ROOT / "dist-app" / "mac" / MAC_TARBALL


def _mac_from_tar(tar_src: Path, folder: str, data_dir: Path,
                  readme: Path, exe_name: str, out: Path,
                  progress=lambda s, p: None) -> int:
    """Write the customer's Mac archive by copying entries, never unpacking them.

    WHY THIS EXISTS
    A macOS Python.framework is a structure of symlinks -- Python points at
    Versions/Current/Python, Versions/Current points at 3.13 -- and the code
    signature seals that structure. Turn the links into copies and macOS refuses
    to load the library at all:

        code signature ... not valid for use in process:
        library load disallowed by system policy

    Which is exactly what happens if the build is zipped, or copied by a Windows
    filesystem that has no symlinks to copy. It happened here: four identical
    6.7 MB Pythons where there should have been one file and three links.

    So nothing is ever extracted on this machine. Each member is copied from the
    Mac's own tar into the customer's tar with its type, mode and link target
    unchanged, and only the data folder is added.
    """
    import io as _io
    import plistlib
    import tarfile

    # What the build called its top-level entry -- read from the archive rather
    # than assumed. It was compared against BRAND_TOKEN, which stopped matching
    # the moment the build started producing "App.app" instead of "App":
    # the rename silently did nothing and the customer's app would have arrived
    # under the name this software was first written under.
    with tarfile.open(tar_src, "r:gz") as probe:
        tops = {n.split("/")[0] for n in probe.getnames() if n and "/" in n} or \
               {n for n in probe.getnames() if n}
        built_as = sorted(tops)[0] if tops else BRAND_TOKEN
    is_app = built_as.endswith(".app")
    renamed_to = f"{exe_name}.app" if is_app else exe_name

    copied = links = 0
    # One pass: a gzipped tar cannot be reopened and appended to, so the app and
    # the customer's data are written together.
    with tarfile.open(tar_src, "r:gz") as src, \
            tarfile.open(out, "w:gz", compresslevel=6) as dst:
        progress("Packing the app", 40)
        app_entries = []
        for member in src:
            parts = member.name.split("/")
            if is_app:
                # <folder>/<Brand>.app/... -- the customer folder is added ABOVE
                # the bundle, never in place of it. Overwriting parts[0] merged
                # the .app's own Contents into the folder and there was no
                # application left, only its insides.
                if parts and parts[0] == built_as:
                    parts[0] = renamed_to
                parts = [folder] + parts
            elif parts and parts[0] != folder:
                # A plain folder build: re-rooting is exactly right.
                parts[0] = folder
            member.name = "/".join(parts)
            if len(parts) == 2 and parts[1] not in app_entries:
                app_entries.append(parts[1])

            # The name Finder shows comes from Info.plist, not the folder, so a
            # renamed .app whose plist still says otherwise reads as the wrong
            # product in Get Info and the menu bar. CFBundleExecutable is left
            # exactly as it is -- it names the binary inside, and changing it
            # stops the app launching at all.
            if is_app and member.name.endswith(f"{renamed_to}/Contents/Info.plist"):
                raw = src.extractfile(member).read()
                try:
                    plist = plistlib.loads(raw)
                    for key in ("CFBundleName", "CFBundleDisplayName"):
                        if key in plist:
                            plist[key] = exe_name
                    raw = plistlib.dumps(plist)
                except Exception:
                    pass                      # ship it unchanged rather than not at all
                member.size = len(raw)
                dst.addfile(member, _io.BytesIO(raw))
                copied += 1
                continue

            if member.issym() or member.islnk():
                dst.addfile(member)          # the link itself, never its target
                links += 1
            elif member.isfile():
                dst.addfile(member, src.extractfile(member))
                copied += 1
            else:
                dst.addfile(member)          # directories and the rest
                copied += 1

        progress("Adding your licence", 80)
        # Inside the .app when there is one -- Contents/Resources/seed-data --
        # because a Mac app is a read-only bundle the customer drags to
        # Applications, and anything left beside it is lost on the first drag.
        # runner.py copies it out to Application Support on first run.
        app_dir = next((n for n in app_entries if n.endswith(".app")), "")
        # The folder is part of the path too. Without it the seed landed at
        # SafeNest.app/... beside the bundle rather than inside it, so the copy
        # arrived with no licence and refused to start.
        prefix = (f"{folder}/{app_dir}/Contents/Resources/seed-data"
                  if app_dir else f"{folder}/data")
        for path in sorted(data_dir.rglob("*")):
            if path.is_file():
                dst.add(path, arcname=f"{prefix}/"
                        f"{path.relative_to(data_dir).as_posix()}")
                copied += 1
        if readme.is_file():
            dst.add(readme, arcname=f"{folder}/README.txt")
            copied += 1

    print(f"[bundle] mac archive: {copied} files, {links} symlinks preserved")
    return links


def _licensed_readme(dest: Path, app_name: str, launcher: str, token: str,
                     platform: str = WINDOWS) -> None:
    """The one page a customer sees before they double-click anything.

    The compiled bundle is an executable and two folders; without this it arrives
    with no instructions, no ownership notice and no statement of what they may do
    with it. Written here rather than copied, because it names the licence holder
    and the launcher, both of which differ per build.

    Built as a list of lines rather than one long literal: the escapes in a
    multi-line f-string are easy to get wrong and produce a file that looks fine
    in review and is mangled on disk.
    """
    from . import licensing
    holder = ""
    try:
        payload = licensing.parse(token, _read_env_value("LICENSE_PUBLIC_KEY_HEX"))
        holder = payload.get("name") or ""
    except Exception:
        pass

    brand = _display_name(app_name)
    who = (f"Licensed to {holder}." if holder
           else "Licensed to the holder of the enclosed licence.")
    lines = [
        brand,
        "=" * len(brand),
        "",
    ]
    if platform == MAC:
        lines += _mac_gatekeeper_note(brand, launcher, dest.name)
    lines += [
        "  STARTING IT",
        f"    Double-click  {launcher}",
        "    The first run asks a few questions and sets everything up. After",
        "    that it starts by itself whenever you switch this computer on.",
        "",
        "  WHERE YOUR RECORDS ARE",
        "    In the 'data' folder next to the program. They never leave this",
        "    computer. Copy that folder to a USB drive now and again - it is",
        "    your only backup, and nobody else holds one.",
        "",
        "  ON YOUR PHONE",
        "    While this computer is on, open the address the program prints when",
        "    it starts, from any device on the same Wi-Fi. To reach it from",
        "    outside the house, see Profile -> Web address inside the app.",
        "",
        "  IF SOMETHING LOOKS WRONG",
        "    Close the window and start the program again. Your records are on",
        "    disk and are not affected by restarting it.",
        "",
        "  ---------------------------------------------------------------",
        f"  (c) {ist.today().year} {brand}. All rights reserved.",
        f"  {who} Not for resale or redistribution.",
        "  This software is licensed, not sold. The licence permits use for the",
        "  period stated in your licence file.",
        "",
    ]
    # CRLF: opened in Notepad on a Windows machine more often than not.
    (dest / "README.txt").write_text("\r\n".join(lines), encoding="utf-8", newline="")


def build_licensed(platform: str, licence_token: str, out_root: Path | None = None,
                   progress=lambda step, pct: None, folder_suffix: str = "",
                   hosting: dict | None = None) -> dict:
    """A customer's copy: the compiled app plus their licence, and no source.

    This is the delivery path for someone who bought a licence. It copies the
    already-compiled executable rather than the source tree, then writes the same
    `data/` files the packaged runner already knows how to read — the licence, an
    empty database, this copy's own vault key, and the branding.

    Deliberately refuses rather than falling back. A licensed build that silently
    shipped source would be the one failure nobody would catch until the code was
    already in someone else's hands.
    """
    if platform not in TEMPLATES:
        raise ValueError(f"platform must be one of {sorted(TEMPLATES)}")
    # Judged on whether THIS platform's binaries are present, not on what this
    # machine can compile. Both can sit here at once, so one laptop issues both.
    #
    # Without the check, asking for a Mac copy on Windows copied the Windows build
    # into a Mac-named folder: a bundle that looks right, zips, sends, and cannot
    # start on the machine it was made for. Found in a real customer bundle.
    # A Mac copy assembled on anything but a Mac must come from the tarball, or
    # the Python.framework symlinks become copies and macOS refuses to load the
    # library at all. There is no way to represent them on this filesystem, so
    # there is no half-measure to fall back to.
    if platform == MAC and host_platform() != MAC and not mac_tarball().is_file():
        raise FileNotFoundError(
            f"The Mac build here cannot be packaged on {host_platform().title()}.\n\n"
            f"macOS builds contain symlinks inside Python.framework, and the code "
            f"signature covers them. Copying them on {host_platform().title()} "
            f"turns them into duplicate files, and the app then dies with "
            f"'library load disallowed by system policy'.\n\n"
            f"Re-run the 'Build the Mac app' workflow — it now packs a tarball "
            f"that keeps them — and put it at:\n"
            f"    {mac_tarball()}")

    if not compiled_available(platform) and not (
            platform == MAC and mac_tarball().is_file()):
        where = compiled_dir(platform)
        if platform == host_platform():
            raise FileNotFoundError(
                "No compiled build found. Run this first, on this machine:\n"
                "    python packaging/build_exe.py --native\n"
                "It compiles the app to machine code so the customer's copy "
                "contains no readable source.")
        raise FileNotFoundError(
            f"There is no {platform.title()} build on this computer yet.\n\n"
            f"A {platform.title()} executable can only be COMPILED on "
            f"{platform.title()} — PyInstaller freezes the interpreter it runs "
            f"under, and no setting changes that. It only has to be compiled "
            f"once per release, though, not maintained there:\n\n"
            f"  1. Build it on a {platform.title()} machine, or let the included\n"
            f"     GitHub Actions workflow do it (.github/workflows/build-mac.yml)\n"
            f"  2. Copy the resulting folder here, to:\n"
            f"        {where}\n\n"
            f"After that this computer issues {platform.title()} copies like any "
            f"other — the source, the licences and the signing key all stay here.")

    source = compiled_dir(platform)

    out_root = Path(out_root) if out_root else default_output_root()
    app_name = current_app_name()
    dest = out_root / (platform_layout(app_name)[platform]["folder"] + folder_suffix)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    # A Mac copy made anywhere but a Mac is assembled straight from the tarball at
    # the end of this function, so there is no app folder to copy here -- only a
    # staging directory for the licence, the database and the README. Copying the
    # app onto this filesystem is precisely what must not happen: it is where the
    # framework symlinks would be flattened.
    from_tar = platform == MAC and host_platform() != MAC
    progress("Copying the compiled app", 10)
    if from_tar:
        dest.mkdir(parents=True)
    else:
        shutil.copytree(source, dest)

    # The executable is named after the software, so the customer sees their own
    # product rather than whatever it was called when it was compiled.
    #
    # Suffixed by the TARGET platform, not by this machine: building a Mac copy
    # from Windows looked for App.exe and renamed it to SafeNest.exe, inside a
    # bundle whose launcher expects a Unix executable with no extension.
    brand = _display_name(app_name)
    suffix = ".exe" if platform == WINDOWS else ""
    exe_name = f"{BRAND_TOKEN}{suffix}"
    wanted = f"{brand}{suffix}"
    if brand != BRAND_TOKEN and (dest / exe_name).exists():
        (dest / exe_name).rename(dest / wanted)
    if platform == MAC:
        # Lost by the copy on Windows, and a Mac will not launch a file it cannot
        # execute. _zip_bundle restores it in the archive too.
        try:
            target = dest / wanted
            if target.is_file():
                target.chmod(0o755)
        except OSError:
            pass

    progress("Preparing a clean database", 45)
    data_dir = dest / "data"
    # A copy of the compiled build may carry a data folder from a test run here.
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True)

    result = {"platform": platform, "folder": str(dest), "media_files": 0,
              "media_bytes": 0, "with_data": False, "scope": "licence",
              "app_name": app_name, "launcher": wanted, "compiled": True}
    result["database"] = _empty_database(data_dir / "finmate.db")
    result["branding"] = _carry_branding(data_dir / "finmate.db", data_dir / "media")

    progress("Adding the licence", 70)
    # A vault key generated for them: this server's key protects everyone else's
    # saved passwords and must never leave the building.
    _write_carried_secrets(data_dir, vault_key=secrets.token_hex(32))
    _carry_licence(data_dir, licence_token)
    result["licensed"] = True
    if hosting and hosting.get("hostname") and hosting.get("tunnel_token"):
        _carry_hosting(data_dir, hosting["hostname"], hosting["tunnel_token"])
        result["hostname"] = hosting["hostname"]

    _licensed_readme(dest, app_name, wanted, licence_token, platform)

    progress("Finishing", 92)
    result["bytes"] = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())

    if platform == MAC and host_platform() != MAC:
        # Built from the Mac's own tarball, so Python.framework's symlinks reach
        # the customer as symlinks. Everything assembled in `dest` above is thrown
        # away except data/ and the README -- the app files come from the archive,
        # untouched, because this filesystem cannot represent them.
        tpath = dest.parent / (dest.name + ".tar.gz")
        if tpath.exists():
            tpath.unlink()
        links = _mac_from_tar(mac_tarball(), dest.name, data_dir,
                              dest / "README.txt", wanted, tpath, progress)
        shutil.rmtree(dest, ignore_errors=True)   # the folder would be misleading
        result["zip"] = str(tpath)
        result["zip_bytes"] = tpath.stat().st_size
        result["bytes"] = result["zip_bytes"]
        result["symlinks"] = links
        result["archive"] = "tar.gz"
        progress("Done", 100)
        return result

    # Always zipped: it is one file to send, and on a Mac the zip is the only way
    # the executable arrives with its executable bit intact.
    zpath = dest.with_suffix(".zip")
    _zip_bundle(dest, zpath, executables={wanted})
    result["zip"] = str(zpath)
    result["zip_bytes"] = zpath.stat().st_size
    progress("Done", 100)
    return result


def build(platform: str, include_data: bool, out_root: Path | None = None,
          progress=lambda step, pct: None, make_zip: bool = False,
          rebuild_frontend: bool = False, user_id: int | None = None,
          folder_suffix: str = "", licence_token: str = "",
          hosting: dict | None = None) -> dict:
    """Create the bundle. `progress(step, percent)` is called as work proceeds.

    With `user_id` set this is a personal export: only that user's rows and files are
    included, and their vault is re-encrypted under a key generated for the copy.

    With `licence_token` set this is a copy for a customer: it carries their signed
    licence and refuses to run once that expires. A licensed build never carries
    the tunnel either — that is the publisher's own domain, not theirs.
    """
    if platform not in TEMPLATES:
        raise ValueError(f"platform must be one of {sorted(TEMPLATES)}")
    if not (FRONTEND / "dist" / "index.html").exists():
        raise FileNotFoundError("frontend/dist is missing — build the web app first.")

    out_root = Path(out_root) if out_root else default_output_root()
    app_name = current_app_name()
    layout = platform_layout(app_name)
    dest = out_root / (layout[platform]["folder"] + folder_suffix)

    progress("Preparing", 3)
    out_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    if rebuild_frontend:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if npm:
            progress("Rebuilding the web app", 8)
            subprocess.run([npm, "run", "build"], cwd=str(FRONTEND), capture_output=True)

    progress("Copying the app", 15)
    _copy_tree(BACKEND_DIR, dest / "backend")

    progress("Copying the web app", 25)
    _copy_tree(FRONTEND / "dist", dest / "frontend" / "dist")

    progress("Adding the launcher", 30)
    # The installer and its instructions are rebranded as they are copied. Doing it
    # here rather than inside setup.py keeps the installer's own logic untouched:
    # the swap is case-sensitive, and every functional string in there — finmate.db,
    # finmate-config.json, the MySQL user — is lower case, so only wording changes.
    brand = _display_name(app_name)
    for name in ("setup.py", "wizard.py", "README.txt"):
        src = BUNDLE_SRC / name
        if not src.exists():
            continue
        if name in REBRAND_FILES:
            text = src.read_text(encoding="utf-8").replace(BRAND_TOKEN, brand)
            # The copyright year is stamped at build time rather than written into
            # the file, so a bundle produced next January does not still claim
            # this one. Runs even when the brand is unchanged — the year moves on
            # regardless of what the app is called.
            text = re.sub(r"\(c\) \d{4} ", f"(c) {ist.today().year} ", text)
            (dest / name).write_text(text, encoding="utf-8", newline="\n")
        else:
            shutil.copy2(src, dest / name)
    _write_launchers(dest, platform, app_name)

    data_dir = dest / "data"
    data_dir.mkdir(exist_ok=True)
    result = {"platform": platform, "folder": str(dest), "media_files": 0, "media_bytes": 0,
              "with_data": bool(include_data), "database": False,
              "scope": "mine" if user_id else "all",
              # Returned so the screen can name the exact file to double-click
              # rather than guessing at one that no longer exists.
              "app_name": app_name, "launcher": layout[platform]["launcher"]}

    if licence_token:
        # Software, not records. No database copy, no media copy, and a vault key
        # generated for them — this server's key protects everyone else's saved
        # passwords and must never leave the building.
        progress("Preparing a clean database", 40)
        result["database"] = _empty_database(data_dir / "finmate.db")
        # The app's name and icon are the one thing that SHOULD travel into an
        # otherwise empty database — without it the copy installs under whatever
        # name the software was compiled with rather than the current one.
        result["branding"] = _carry_branding(data_dir / "finmate.db", data_dir / "media")
        _write_carried_secrets(data_dir, vault_key=secrets.token_hex(32))
        result["with_data"] = False
    elif include_data:
        progress("Exporting your records", 40)
        if user_id:
            from . import userexport
            summary = userexport.export_for_user(data_dir / "finmate.db", user_id)
            result["database"] = True
            result["rows"] = summary["rows"]
            result["unreadable_vault_fields"] = summary["unreadable_vault_fields"]
            _write_carried_secrets(data_dir, vault_key=summary["vault_key"])
        else:
            result["database"] = _export_database(data_dir / "finmate.db")
            _write_carried_secrets(data_dir)
            # A licensed copy is somebody else's installation, so it must not
            # arrive holding the keys to this one's public address.
            result["tunnel"] = False if licence_token else _carry_tunnel(data_dir)
        progress("Copying photos and documents", 45)
        result["media_files"], result["media_bytes"] = _copy_media(
            data_dir / "media", progress, user_id=user_id)

    if licence_token:
        _carry_licence(data_dir, licence_token)
        result["licensed"] = True
        if hosting and hosting.get("hostname") and hosting.get("tunnel_token"):
            _carry_hosting(data_dir, hosting["hostname"], hosting["tunnel_token"])
            result["hostname"] = hosting["hostname"]

    progress("Finishing", 96)
    result["bytes"] = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())

    # A Mac bundle is always zipped: it is the only way the launcher arrives with
    # its executable bit intact. On Windows the plain folder is fine, so the zip is
    # optional there.
    if make_zip or platform == MAC:
        progress("Compressing", 98)
        zpath = dest.parent / f"{dest.name}-{ist.today():%Y-%m-%d}.zip"
        _zip_bundle(dest, zpath, executables={layout[MAC]["launcher"]})
        result["zip"] = str(zpath)
        result["zip_bytes"] = zpath.stat().st_size

    progress("Done", 100)
    return result
