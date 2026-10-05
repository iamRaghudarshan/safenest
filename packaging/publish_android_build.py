"""Put a CI-built Android APK where the phone can find it.

THE STEP THAT WAS NEVER WIRED. `routers/mobile.py` has said so since it was
written: "A later step wires the desktop release process to drop the CI-built
APK here." Until then, tagging a release produced a GitHub artifact and nothing
else — the phone kept polling /api/mobile/latest, kept being told 1.86.0, and
kept showing 1.86.0 while two newer builds sat in Actions. That is exactly how
it went unnoticed: every part worked, and no part joined them up.

WHAT PUBLISHING IS. Two files beside the app:

    mobile/android.json     {"version", "build", "notes", "filename"}
    mobile/app-release.apk

`mobile.py` reads both FROM DISK ON EVERY REQUEST, so dropping them here is the
whole publish — no restart, nothing to reload. That is also why the APK is
written to a temporary name and moved into place: a half-written file would
otherwise be served to a phone mid-download.

THE TOKEN comes from backend/.env, the same GITHUB_TOKEN that fetch_mac_build.py
uses — a fine-grained token with Actions: read. It is never printed. If it is
scoped to the server repository only, it cannot see the phone's, and this says
so plainly instead of failing as a 404.

    python packaging/publish_android_build.py                 # newest android-* build
    python packaging/publish_android_build.py --tag android-1.88.0
    python packaging/publish_android_build.py --notes "What changed."
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "backend" / ".env"
DEST = ROOT / "mobile"
ARTIFACT = "safenest-android"
DEFAULT_REPO = "iamRaghudarshan/safenest-mobile"
API = "https://api.github.com"
UA = "safenest-publish-android"


class Stop(Exception):
    """Something the person running this can fix, printed without a traceback."""


def env(key: str) -> str:
    if not ENV.is_file():
        return ""
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Artifact downloads redirect to blob storage, which rejects our header.

    Copied in spirit from fetch_mac_build.py, and for the same reason: urllib
    forwards every header to the new host, that host returns 403 for an
    Authorization it never asked for, and it reads as "your token is wrong".
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
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise Stop(
                "GitHub refused the token. It needs Actions: read on the PHONE'S\n"
                "repository, which is a different one from this server's — a token\n"
                "scoped to safenest alone cannot see safenest-mobile.\n"
                "  https://github.com/settings/personal-access-tokens")
        if exc.code == 404:
            raise Stop("GitHub returned 404 — either the repository name is wrong "
                       "or the token cannot see it.")
        raise Stop(f"GitHub returned {exc.code} for {url}")


def download(token: str, url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
    })
    opener = urllib.request.build_opener(_DropAuthOnRedirect())
    with opener.open(req, timeout=900) as resp:
        return resp.read()


def pick_run(token: str, slug: str, tag: str | None) -> dict:
    """The newest SUCCESSFUL run that built an APK.

    Only `android-*` tags build one: a `v*` tag spends a macOS runner and the
    Android job is gated off for it, so a `v*` run is green with no artifact.
    Picking by "newest green run" without that filter would hand back an iOS
    build and publish nothing.
    """
    runs = api(token, f"/repos/{slug}/actions/runs?per_page=50").get("workflow_runs", [])
    for r in runs:
        if r.get("conclusion") != "success":
            continue
        branch = str(r.get("head_branch") or "")
        if tag:
            if branch == tag:
                return r
            continue
        if branch.startswith("android-"):
            return r
    raise Stop(f"No successful {'run for ' + tag if tag else 'android-* run'} found.")


def version_from(run: dict) -> str:
    m = re.match(r"android-(.+)$", str(run.get("head_branch") or ""))
    if not m:
        raise Stop("Could not read a version from the tag "
                   f"{run.get('head_branch')!r}; pass --version.")
    return m.group(1)


def apk_from_zip(blob: bytes) -> tuple[str, bytes]:
    """The APK inside the artifact zip, checked rather than assumed.

    VERIFIED HERE AND NOT IN CI, because a green tick only proves the build ran.
    An artifact that unzips to nothing, or to something that is not an APK, would
    otherwise be published and would fail on the phone with no clue why.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".apk")]
        if not names:
            raise Stop(f"The artifact holds no .apk — it has: {z.namelist()[:10]}")
        # Prefer the plain release name when CI produces several slices.
        names.sort(key=lambda n: (0 if n.endswith("app-release.apk") else 1, len(n)))
        return names[0], z.read(names[0])


def check_apk(data: bytes) -> str:
    """Confirm it really is an installable APK. Returns a one-line description."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        raise Stop("The extracted file is not a valid zip, so it is not an APK.")
    for needed in ("AndroidManifest.xml", "classes.dex"):
        if needed not in names:
            raise Stop(f"The APK has no {needed} — it is not a complete build.")
    # ONLY THE ABIs THAT CAN ACTUALLY RUN, which is not the same as the folders
    # present. A release APK carries arm64-v8a and armeabi-v7a; an x86_64 folder
    # also appears, holding two files from a plugin and no engine at all. Listing
    # folders made this script report "ABIs: ..., x86_64" — which reads as "it
    # runs on an x86_64 emulator", and it does not: installing it there dies with
    # `libflutter.so is for EM_AARCH64 instead of EM_X86_64`. A verification tool
    # that overstates what it checked is worse than one that checks nothing.
    runnable = sorted({n.split("/")[1] for n in names
                       if n.startswith("lib/") and n.endswith("/libflutter.so")})
    if not runnable:
        raise Stop("The APK carries no libflutter.so — the engine is missing.")
    return f"{len(names)} entries, runnable on: {', '.join(runnable)}"


def publish(version: str, build: int, notes: str, apk: bytes, filename: str) -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    # The APK first and atomically: mobile.py reads from disk per request, so a
    # partially written file is a file a phone can be served mid-download.
    tmp = DEST / (filename + ".part")
    tmp.write_bytes(apk)
    os.replace(tmp, DEST / filename)
    # The manifest last, so it never advertises a build that is not there yet.
    (DEST / "android.json").write_text(
        json.dumps({"version": version, "build": build,
                    "notes": notes, "filename": filename}, indent=2) + "\n",
        encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Publish a CI-built APK to mobile/.")
    p.add_argument("--tag", help="e.g. android-1.88.0 (default: newest android-*)")
    p.add_argument("--repo", default=env("MOBILE_REPO") or DEFAULT_REPO)
    p.add_argument("--version", help="override the version read from the tag")
    p.add_argument("--notes", default="", help="what changed, shown in the prompt")
    args = p.parse_args()

    token = env("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise Stop("No GITHUB_TOKEN in backend/.env. fetch_mac_build.py documents "
                   "the same token; Actions: read is all it needs.")

    print(f"Looking in {args.repo}...")
    run = pick_run(token, args.repo, args.tag)
    version = args.version or version_from(run)
    build = int(run.get("run_number") or 0)
    print(f"  run {build}, tag {run.get('head_branch')}, "
          f"commit {str(run.get('head_sha'))[:9]}")

    arts = api(token, f"/repos/{args.repo}/actions/runs/{run['id']}/artifacts")
    match = [a for a in arts.get("artifacts", [])
             if a.get("name") == ARTIFACT and not a.get("expired")]
    if not match:
        raise Stop(f"That run has no live {ARTIFACT!r} artifact. Artifacts expire; "
                   "re-run the build if it has.")

    print(f"  downloading {match[0]['size_in_bytes'] / 1048576:.1f} MB...")
    blob = download(token, match[0]["archive_download_url"])
    name, apk = apk_from_zip(blob)
    print(f"  {name}: {len(apk) / 1048576:.1f} MB — {check_apk(apk)}")

    notes = args.notes.strip() or f"SafeNest {version}."
    publish(version, build, notes, apk, "app-release.apk")
    print(f"\nPublished {version} (build {build}) to {DEST}")
    print("No restart needed — mobile.py reads these from disk on every request.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(2)
