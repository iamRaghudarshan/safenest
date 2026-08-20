"""Shipping a new version to copies already in customers' hands.

THE SHAPE OF IT
Same arrangement as licences, for the same reason: the publisher signs, the
customer verifies offline, and nothing trusts the network. A release manifest is
an Ed25519-signed statement of "version X, this many bytes, this SHA-256". A copy
that downloads something which does not match the manifest throws it away.

That signature is the whole security story. An update replaces the executable a
customer runs against their entire financial history, so "we fetched it over
HTTPS" is not enough — HTTPS says the bytes arrived unaltered from whoever
answered, not that it was us who answered. The manifest is signed by the same key
that signs licences, which the customer's copy already holds the public half of.

THE DATA IS NOT PART OF THE UPDATE
Only the program is replaced: the executable and _internal. `data/` — the
database, the media, the licence, the vault key — is never touched, and the
staged copy is unpacked somewhere else entirely so a half-finished download
cannot land on top of anything. Schema changes are handled on the next launch by
main.py::_sqlite_topup(), which backs the database up before it alters it.

WHY THE SWAP NEEDS A HELPER SCRIPT
Windows will not let a running program overwrite its own .exe. So the app writes
a small script, starts it, and exits; the script waits for the process to go,
swaps the folders, and starts the new version. There is no way around this on
Windows and it is what every desktop updater does.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from . import ist
from .licensing import _b64, _canonical, _private_key, _public_key, _unb64

PREFIX = "FMUPD"
CHUNK = 1024 * 256


class UpdateError(Exception):
    """Something a person can act on — shown to them as written."""


# ------------------------------------------------------------ publisher: sign
def sign(private_key_hex: str, *, version: str, sha256: str, size: int,
         notes: str = "", filename: str = "") -> tuple[str, dict]:
    """Sign a release manifest. Returns (token, payload)."""
    payload = {
        "version": version.strip(),
        "sha256": sha256.lower(),
        "size": int(size),
        "notes": (notes or "").strip()[:2000],
        "filename": filename or "",
        "released": ist.today().isoformat(),
    }
    signature = _private_key(private_key_hex).sign(_canonical(payload))
    return f"{PREFIX}.{_b64(_canonical(payload))}.{_b64(signature)}", payload


# --------------------------------------------------------- customer: verify
def verify(token: str, public_key_hex: str) -> dict:
    """Check a manifest's signature. Raises UpdateError rather than returning junk.

    Raises rather than returning a state, unlike licensing.parse(): a licence that
    cannot be read is a screen to show someone, but an update that cannot be
    verified is simply not applied, and there is nothing to display about it.
    """
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise UpdateError("That is not a release manifest.")
    try:
        payload = json.loads(_unb64(parts[1]))
        _public_key(public_key_hex).verify(_unb64(parts[2]), _canonical(payload))
    except UpdateError:
        raise
    except Exception:
        raise UpdateError("This update was not signed by your supplier. "
                          "It has been discarded.")
    if not payload.get("version") or not payload.get("sha256"):
        raise UpdateError("This update's details are incomplete.")
    return payload


def digest(path: Path, progress=lambda done, total: None) -> str:
    """SHA-256 of a file, read in chunks so a 400 MB build does not sit in memory."""
    total = path.stat().st_size
    done = 0
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
            done += len(block)
            progress(done, total)
    return h.hexdigest()


# --------------------------------------------------------------- versions
def current_version() -> str:
    """What this copy is running.

    APP_VERSION is set by runner.py in a packaged copy. When it is missing (running
    from source, or a build that didn't stamp it) fall back to a version file rather
    than returning "0" — several callers report this as the app version, and "0" (or
    a hard-coded constant) is exactly what made a 3.14 copy look like 2.0."""
    v = (os.environ.get("APP_VERSION") or "").strip()
    if v:
        return v
    for p in (Path(sys.executable).resolve().parent / "version.txt",
              Path(__file__).resolve().parents[2] / "VERSION"):
        try:
            t = p.read_text().strip()
            if t:
                return t
        except Exception:
            pass
    return "0"


def is_newer(candidate: str, running: str) -> bool:
    """Compare dotted versions numerically, so 2.10 beats 2.9.

    String comparison gets that backwards, and the first customer to see it would
    be told they were up to date on a version months behind.
    """
    def parts(v):
        out = []
        for chunk in str(v or "0").split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            out.append(int(digits or 0))
        return out
    a, b = parts(candidate), parts(running)
    length = max(len(a), len(b))
    a += [0] * (length - len(a))
    b += [0] * (length - len(b))
    return a > b


# ------------------------------------------------------- customer: install
def install_root() -> Path | None:
    """What an update replaces: the .app bundle, or the folder holding the program.

    On macOS this is the whole `.app`, not the directory the executable sits in --
    that is Contents/MacOS, inside the bundle. Looking for `_internal` there found
    nothing (PyInstaller puts it in Contents/Frameworks) and the updater refused
    every Mac copy with "Updates only apply to an installed copy".

    Replacing the whole bundle is also the only correct unit: a .app is signed as
    one thing, and swapping pieces of it invalidates the signature.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    root = exe.parent
    return root if (root / "_internal").is_dir() else None


def is_app_bundle(path: Path | None = None) -> bool:
    p = path or install_root()
    return bool(p and p.suffix == ".app")


def staging_dir() -> Path:
    """Where a download is unpacked. Beside the app, never inside it.

    Inside would put a half-finished copy of the program in the folder the program
    is being read from, which is exactly the state the swap exists to avoid.
    """
    root = install_root() or Path.cwd()
    return root.parent / f".{root.name}-update"


def this_platform() -> str:
    """Which build this copy needs. Sent when asking what is on offer.

    A Windows zip is not an update for a Mac. The publisher used to have one
    "current release" for everybody, so a Mac customer pressing update downloaded
    a Windows program and the installer refused it -- correctly, but only after
    the whole download.
    """
    return "mac" if sys.platform == "darwin" else "windows"


def unpack(archive: Path, into: Path) -> Path:
    """Extract the release, refusing any entry that points outside the folder.

    An archive is untrusted even when the manifest is signed -- the signature says
    who built it, not that every path inside is sane. An entry named
    ../../Windows/System32/... would otherwise be written exactly there.

    A Mac release arrives as a tar.gz rather than a zip, and has to: a zip cannot
    carry the symlinks inside Python.framework, and the code signature seals that
    structure. Flatten them and macOS refuses to load the interpreter at all.
    """
    if into.exists():
        shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True)

    if tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            root = into.resolve()
            for member in tf.getmembers():
                target = (into / member.name).resolve()
                if not str(target).startswith(str(root)):
                    raise UpdateError("This update contains an unsafe file path "
                                      "and has been discarded.")
                # A link is a path too, and the one that does not appear in the
                # member's own name. Left unchecked, an entry could link out of
                # the folder and the next write through it lands anywhere.
                if member.issym() or member.islnk():
                    dest = (Path(member.name).parent / member.linkname)
                    if not str((into / dest).resolve()).startswith(str(root)):
                        raise UpdateError("This update contains a link pointing "
                                          "outside itself and has been discarded.")
        # The paths and links were validated above; extract with NATIVE tar where
        # it exists. Python's tarfile with filter="data" re-checks and chmods
        # every member, and on macOS — where the OS scans each of ~3,400 extracted
        # files — that crawled for HOURS and left a customer stuck at "Unpacking…"
        # for two. Native tar does the same job in seconds and preserves the
        # Python.framework symlinks the bundle's signature depends on. The curl
        # installer already uses native tar, which is why it never hung.
        if os.name != "nt" and shutil.which("tar"):
            subprocess.run(["tar", "-xzf", str(archive), "-C", str(into)],
                           check=True, timeout=1200)
        else:
            # `data` is the strict filter: it drops device nodes, setuid bits and
            # absolute paths. Only reached on Windows / a box without tar.
            with tarfile.open(archive) as tf2:
                try:
                    tf2.extractall(into, filter="data")
                except TypeError:                   # older Pythons: no filters
                    tf2.extractall(into)
    else:
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                target = (into / member).resolve()
                if not str(target).startswith(str(into.resolve())):
                    raise UpdateError("This update contains an unsafe file path "
                                      "and has been discarded.")
            zf.extractall(into)
    # A bundle unzips to a single folder; use it if that is what we got.
    entries = [p for p in into.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return into


def _swap_script(new_root: Path, app_root: Path, exe: Path) -> Path:
    """The script that does the replacing once this program has exited."""
    if os.name == "nt":
        script = app_root.parent / f".{app_root.name}-apply.cmd"
        script.write_text(
            "@echo off\r\n"
            "rem Written by the app. Waits for it to close, swaps the program\r\n"
            "rem files, and starts the new version. data\\ is never touched.\r\n"
            f'cd /d "{app_root.parent}"\r\n'
            ":wait\r\n"
            f'tasklist /FI "IMAGENAME eq {exe.name}" | find /I "{exe.name}" >nul\r\n'
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >nul\r\n"
            "  goto wait\r\n"
            ")\r\n"
            # Only the program. Anything else in the folder -- above all data\ --
            # is left exactly where it is.
            f'if exist "{app_root}\\_internal" rmdir /s /q "{app_root}\\_internal"\r\n'
            f'robocopy "{new_root}" "{app_root}" /E /XD data /NFL /NDL /NJH /NJS >nul\r\n'
            f'start "" "{exe}"\r\n'
            f'rmdir /s /q "{new_root.parent}"\r\n'
            'del "%~f0"\r\n',
            encoding="ascii")
        return script

    script = app_root.parent / f".{app_root.name}-apply.sh"
    if app_root.suffix == ".app":
        # Replace the bundle whole, and use `ditto` rather than `cp`: it keeps the
        # symlinks inside Python.framework and the extended attributes the code
        # signature is stored in. `cp -R` would flatten the first and drop the
        # second, and macOS then refuses to load the app at all.
        #
        # Records are not in here -- they live in ~/Library/Application Support --
        # so replacing the bundle cannot touch them.
        body = (
            "#!/bin/sh\n"
            f'while pgrep -f "{app_root.name}" >/dev/null 2>&1; do sleep 1; done\n'
            "sleep 1\n"
            f'rm -rf "{app_root}.old"\n'
            # Moved aside rather than deleted: if the copy fails there is still a
            # working application to put back, instead of none at all.
            f'mv "{app_root}" "{app_root}.old" 2>/dev/null\n'
            f'ditto "{new_root}" "{app_root}"\n'
            f'if [ ! -d "{app_root}/Contents/MacOS" ]; then\n'
            f'  rm -rf "{app_root}"; mv "{app_root}.old" "{app_root}"\n'
            f'  xattr -dr com.apple.quarantine "{app_root}" 2>/dev/null\n'
            f'  open "{app_root}"; rm -- "$0"; exit 0\n'
            "fi\n"
            # One release goes to every Mac customer, so the bundle inside it is
            # named for the build and not for them. Copied in as-is, the folder
            # stays <Their Name>.app but the Dock, the menu bar and the app
            # switcher all read the build's name -- so an update quietly renames
            # the product on their machine.
            #
            # So the names are read off the copy being replaced and written back
            # afterwards, which is why the old bundle is kept until this is done.
            #
            # From the old Info.plist and NOT from the executable's filename. The
            # bundler leaves the executable called "App" and puts the product name
            # only in the plist, so deriving it from the file would have set the
            # Dock to "App" and called that a fix.
            #
            # Editing inside a bundle breaks its signature, and on macOS that means
            # the interpreter will not load at all, so it is re-signed. If any of
            # that fails the old plist goes back and the app is left exactly as
            # ditto produced it: a wrong name in the Dock is a blemish, an app that
            # will not start is not.
            f'PL="{app_root}/Contents/Info.plist"\n'
            f'OLDPL="{app_root}.old/Contents/Info.plist"\n'
            'if [ -f "$OLDPL" ] && [ -f "$PL" ]; then\n'
            '  cp -p "$PL" "$PL.bak"\n'
            '  CHANGED=0\n'
            '  for k in CFBundleName CFBundleDisplayName; do\n'
            '    WAS=$(/usr/libexec/PlistBuddy -c "Print :$k" "$OLDPL" 2>/dev/null)\n'
            '    NOW=$(/usr/libexec/PlistBuddy -c "Print :$k" "$PL" 2>/dev/null)\n'
            '    if [ -n "$WAS" ] && [ "$WAS" != "$NOW" ]; then\n'
            '      /usr/libexec/PlistBuddy -c "Set :$k $WAS" "$PL" 2>/dev/null'
            ' && CHANGED=1\n'
            '    fi\n'
            '  done\n'
            '  if [ "$CHANGED" = "1" ]; then\n'
            f'    if codesign --force --deep --sign - "{app_root}" 2>/dev/null; then\n'
            '      rm -f "$PL.bak"\n'
            '    else\n'
            '      mv "$PL.bak" "$PL"\n'
            '    fi\n'
            '  else\n'
            '    rm -f "$PL.bak"\n'
            '  fi\n'
            "fi\n"
            f'rm -rf "{app_root}.old"\n'
            f'xattr -dr com.apple.quarantine "{app_root}" 2>/dev/null\n'
            f'open "{app_root}"\n'
            f'rm -rf "{new_root.parent}"\n'
            'rm -- "$0"\n')
    else:
        body = (
            "#!/bin/sh\n"
            f'while pgrep -f "{exe.name}" >/dev/null 2>&1; do sleep 1; done\n'
            f'rm -rf "{app_root}/_internal"\n'
            f'ditto "{new_root}" "{app_root}"\n'
            f'chmod +x "{exe}" 2>/dev/null\n'
            f'open "{exe}" 2>/dev/null || "{exe}" &\n'
            f'rm -rf "{new_root.parent}"\n'
            'rm -- "$0"\n')
    script.write_text(body, encoding="ascii")
    script.chmod(0o755)
    return script


def apply_staged(new_root: Path) -> Path:
    """Hand the swap to a helper and return the script's path.

    The caller is expected to stop the server immediately afterwards: the script
    is already waiting for this process to disappear.
    """
    app_root = install_root()
    if app_root is None:
        raise UpdateError("Updates only apply to an installed copy.")
    exe = Path(sys.executable).resolve()

    if is_app_bundle(app_root):
        # The whole bundle is the unit. Find the .app inside what was unpacked --
        # its name may differ from the installed one if the product was renamed
        # between releases, and matching on the old name would reject a perfectly
        # good update.
        # The archive holds one folder, so unpack() has already stepped into it —
        # meaning new_root IS the .app, not something containing one. Searching
        # inside it for a bundle found nothing and refused the update with "this
        # update does not contain an application", on an archive that contained
        # exactly that. It failed the same way every time, so the Mac updater had
        # never once completed.
        if new_root.suffix == ".app" and new_root.is_dir():
            source = new_root
        else:
            found = [p for p in new_root.rglob("*.app") if p.is_dir()]
            found = [p for p in found if ".app/" not in str(p.relative_to(new_root))]
            if not found:
                raise UpdateError("This update does not contain an application.")
            source = found[0]
    else:
        if not (new_root / exe.name).exists():
            # Renamed per customer, so a mismatch means the wrong archive entirely.
            names = [p.name for p in new_root.iterdir() if p.suffix in (".exe", "")]
            raise UpdateError(f"This update does not contain {exe.name} "
                              f"(found: {', '.join(names[:4]) or 'nothing'}).")
        source = new_root

    script = _swap_script(source, app_root, exe)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
            getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags,
                         close_fds=True)
    else:
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                         close_fds=True)
    return script
