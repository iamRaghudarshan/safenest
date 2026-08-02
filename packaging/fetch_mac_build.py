"""Bring the Mac build back from CI and put it where copies are issued from.

    python packaging/fetch_mac_build.py

WHY THIS EXISTS
Compiling for macOS has to happen on macOS -- PyInstaller freezes the interpreter
it runs under -- so the Mac half of every release is built by GitHub's runners and
has to be carried back here by hand. That hand step was the weak point: a release
went out with the Windows half on the new version and the Mac half silently still
on the old one, and nothing anywhere said so until it reached a customer.

So this refuses to install an archive whose version does not match VERSION, unless
told otherwise. A loud stop here is the only place that mismatch is catchable.

WHY IT WAITS
It is meant to be run straight after pushing a version bump, when the build is
still going. Taking the newest *finished* run at that moment would fetch the
previous release -- the exact failure above, arrived at by being helpful.

THE TOKEN
Read from backend/.env, which never ships: `bundler.SKIP_FILES` excludes it, the
same way the licence signing key beside it never travels. Deliberately not a
`config.py` setting -- the app has no business holding a credential for the
publisher's source repository, and a setting it never loads is one no endpoint can
ever return.

    GITHUB_TOKEN=github_pat_...

A fine-grained token, one repository, Actions read-only. Nothing else is needed.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "backend" / ".env"
DEST = ROOT / "dist-app" / "mac"
TARBALL = "mac-app.tar.gz"
WORKFLOW = "build-mac.yml"
API = "https://api.github.com"


class Stop(Exception):
    """Something the person running this can fix, printed without a traceback."""


# --------------------------------------------------------------- small helpers
def env(key: str) -> str:
    if not ENV.is_file():
        return ""
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def local_version() -> str:
    f = ROOT / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


def repo() -> str:
    """owner/name, taken from the git remote so it survives a rename or a fork."""
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        raise Stop("Could not read the git remote, so I do not know which "
                   "repository to ask.")
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        raise Stop(f"The git remote is not a GitHub address: {url}")
    return m.group(1)


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Artifact downloads redirect to blob storage, which rejects our header.

    GitHub answers the download URL with a 302 to a signed storage address. urllib
    forwards every header to the new host by default, and that host returns 403
    for an Authorization it did not ask for -- which reads as "your token is
    wrong" and is not.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers = {k: v for k, v in new.headers.items()
                           if k.lower() != "authorization"}
        return new


def api(token: str, path: str) -> dict:
    url = path if path.startswith("http") else f"{API}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "safenest-fetch-mac",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise Stop("GitHub refused the token. Check it has not expired and "
                       "that it grants Actions: read on this repository.\n"
                       "  https://github.com/settings/personal-access-tokens")
        if exc.code == 404:
            raise Stop("GitHub returned 404. Either the repository name is wrong "
                       "or the token cannot see it.")
        raise Stop(f"GitHub returned {exc.code} for {url}")


def download(token: str, url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "safenest-fetch-mac",
    })
    opener = urllib.request.build_opener(_DropAuthOnRedirect())
    chunks, got, started, last = [], 0, time.time(), -1
    # A console redraws in place; a log file or a pipe gets one line per 10%, or
    # 280 rewrites of the same line arrive as 280 lines of noise.
    tty = sys.stdout.isatty()
    with opener.open(req, timeout=900) as resp:
        total = int(resp.headers.get("content-length") or 0)
        while True:
            block = resp.read(1024 * 512)
            if not block:
                break
            chunks.append(block)
            got += len(block)
            if not total:
                continue
            step = got * 100 // total
            if tty or step // 10 > last // 10:
                last = step
                rate = got / max(time.time() - started, 0.1) / 1048576
                # The rate is the point: this is a 140 MB download and on a slow
                # line it takes twenty minutes. Without it, a working fetch and a
                # stalled one look identical.
                line = (f"  {got // 1048576} of {total // 1048576} MB "
                        f"({step}%, {rate:.1f} MB/s)")
                print(f"\r{line}     " if tty else line, end="" if tty else "\n",
                      flush=True)
    if tty:
        print()
    return b"".join(chunks)


# ------------------------------------------------------------------- the work
def pick_run(token: str, slug: str, wait: int) -> dict:
    """The run to take the build from, waiting out one that is still going."""
    deadline = time.time() + wait
    told = False
    while True:
        runs = api(token, f"/repos/{slug}/actions/workflows/{WORKFLOW}/runs"
                          "?per_page=5").get("workflow_runs") or []
        if not runs:
            raise Stop("That repository has never run the Mac build.\n"
                       f"  https://github.com/{slug}/actions")
        newest = runs[0]
        if newest["status"] == "completed":
            if newest["conclusion"] != "success":
                raise Stop(f"The newest Mac build {newest['conclusion']}. "
                           f"Nothing has been changed here.\n  {newest['html_url']}")
            return newest
        if time.time() > deadline:
            raise Stop("The Mac build is still running and I have waited long "
                       f"enough. Try again in a few minutes.\n  {newest['html_url']}")
        if not told:
            print(f"  a build is still running — waiting for it")
            print(f"  {newest['html_url']}")
            told = True
        time.sleep(20)


def extract_tarball(blob: bytes) -> bytes:
    """The artifact is a zip holding the tar.gz. Pull the tar out, unopened.

    Never unpacked on the way through: the symlinks inside Python.framework
    cannot exist on a Windows filesystem, and the code signature seals that
    structure. Flatten them and macOS refuses to load the interpreter at all --
    which shows up on the customer's machine and nowhere before it.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".tar.gz")]
        if not names:
            raise Stop("That build's artifact holds no tar.gz. It may have been "
                       "produced by an older version of the workflow.")
        return zf.read(names[0])


def inspect(tar_bytes: bytes) -> tuple[str, int]:
    """Version and symlink count, read from the tar without writing anything."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        members = tf.getmembers()
        links = sum(1 for m in members if m.issym())
        vt = [m for m in members if m.name.endswith("/Contents/MacOS/version.txt")]
        version = tf.extractfile(vt[0]).read().decode().strip() if vt else ""
    return version, links


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch the Mac build from CI.")
    ap.add_argument("--any-version", action="store_true",
                    help="install even if it does not match VERSION")
    ap.add_argument("--wait", type=int, default=1800,
                    help="seconds to wait for a build in progress (default 1800)")
    args = ap.parse_args()

    token = env("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise Stop("No GITHUB_TOKEN in backend/.env.\n\n"
                   "Make one at https://github.com/settings/personal-access-tokens\n"
                   "  Repository access : only this repository\n"
                   "  Permissions       : Actions -> Read-only\n\n"
                   "Then add a line to backend/.env:\n"
                   "  GITHUB_TOKEN=github_pat_...")

    slug = repo()
    want = local_version()
    print(f"  repository : {slug}")
    print(f"  this copy  : version {want or 'unknown'}")

    run = pick_run(token, slug, args.wait)
    print(f"  build      : {run['display_title']}  ({run['head_sha'][:7]})")

    arts = api(token, f"/repos/{slug}/actions/runs/{run['id']}/artifacts")
    items = arts.get("artifacts") or []
    if not items:
        raise Stop("That build kept no artifact. GitHub deletes them after a "
                   "while — run the workflow again.\n  " + run["html_url"])
    if items[0].get("expired"):
        raise Stop("That build's artifact has expired. Run the workflow again.\n"
                   "  " + run["html_url"])

    tar_bytes = extract_tarball(download(token, items[0]["archive_download_url"]))
    version, links = inspect(tar_bytes)
    print(f"  contains   : version {version or 'unknown'}, {links} symlinks")

    # A flattened archive looks completely healthy from here and dies on the
    # customer's Mac with a message that names none of this. Zero is the defect.
    if links == 0:
        raise Stop("That archive has no symlinks in it, so the Python framework "
                   "inside was flattened and macOS will refuse to load it. "
                   "Nothing has been changed here.")
    if want and version and version != want and not args.any_version:
        raise Stop(f"That build is version {version} but this copy is {want}, so "
                   f"installing it would ship a Mac copy a release behind the "
                   f"Windows one.\n\nPush the version bump and let the Mac build "
                   f"run, or pass --any-version if you meant this.")

    DEST.mkdir(parents=True, exist_ok=True)
    out = DEST / TARBALL
    # Written beside the target and moved into place, so an interrupted download
    # cannot leave a half-written archive that looks like a usable build.
    tmp = out.with_suffix(".part")
    tmp.write_bytes(tar_bytes)
    if out.exists():
        out.unlink()
    shutil.move(str(tmp), str(out))
    print(f"\n  installed  : {out}  ({out.stat().st_size / 1048576:.0f} MB)")
    print("\n  Mac copies can now be issued from the Licences screen.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as exc:
        print(f"\n  {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n  stopped\n")
        sys.exit(1)
