"""Put a CI-built APK where the phones can reach it.

CLAUDE.md step 4 of a release — "fetch the APK to the server" — was the one
step with no script behind it, so it was done by hand or not at all: the
served build sat at 1.62.0 while the code was thirteen versions past it, and
nothing anywhere said so.

What it does, in order, and it stops at the first thing that is not true:

  * find the android-<version> run for this version and check it SUCCEEDED
  * download its artifact (a zip) and take the APK out of it
  * check the APK is really an APK, and really the version claimed
  * write it beside a new android.json, both atomically

Nothing needs restarting afterwards. `mobile.py` reads the folder per
request, which is the whole reason the release works this way.

    venv/Scripts/python.exe tools/publish_apk.py 1.75.0
    venv/Scripts/python.exe tools/publish_apk.py 1.75.0 --notes "..."
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = "iamRaghudarshan/safenest-mobile"
API = "https://api.github.com"


def token() -> str:
    """The Actions read-only token from the server's own .env.

    Deliberately read-only per CLAUDE.md — it cannot dispatch a workflow, and
    must not be widened. Downloading an artifact is all that is wanted here.
    """
    env = BACKEND / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("GITHUB_TOKEN", "")


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Strip the GitHub token when a download bounces to blob storage.

    An artifact download does not serve the bytes: it answers 302 to a signed
    storage URL. urllib re-sends every header on the redirect, so the GitHub
    Authorization goes to a host that has never heard of it and the whole
    thing fails with 401 — which reads exactly like a bad token and sends you
    off regenerating a token that was fine all along. The signature in the
    redirect URL is the credential; there must be no header beside it.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nxt = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nxt is not None:
            for h in ("Authorization", "authorization"):
                nxt.headers.pop(h, None)
                nxt.unredirected_hdrs.pop(h, None)
        return nxt


_opener = urllib.request.build_opener(_DropAuthOnRedirect)


def get(url: str, tok: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "safenest-publish",
        **({"Authorization": "Bearer " + tok} if tok else {}),
    })
    with _opener.open(req, timeout=300) as r:
        return r.read()


def die(msg: str) -> None:
    print("STOP: " + msg)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--notes", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    version = args.version.lstrip("v")
    tag = "android-" + version

    tok = token()
    if not tok:
        die("no GITHUB_TOKEN in backend/.env — the artifact cannot be fetched")

    print("looking for the %s run..." % tag)
    runs = json.loads(get("%s/repos/%s/actions/runs?per_page=30" % (API, REPO), tok))
    run = next((r for r in runs.get("workflow_runs", [])
                if r.get("head_branch") == tag), None)
    if run is None:
        die("no CI run for tag %s — was it pushed?" % tag)
    if run["status"] != "completed":
        die("run %s is still %s — wait for it" % (run["run_number"], run["status"]))
    if run["conclusion"] != "success":
        # Publishing a build whose tests did not pass is the one thing this
        # script must never make easy.
        die("run %s concluded '%s', not success" % (run["run_number"],
                                                    run["conclusion"]))
    build = run["run_number"]
    print("  run %s succeeded (build %s)" % (run["id"], build))

    arts = json.loads(get(run["artifacts_url"], tok)).get("artifacts", [])
    art = next((a for a in arts if not a.get("expired")), None)
    if art is None:
        die("the run has no artifact left — they expire")
    print("  artifact '%s', %.1f MB" % (art["name"], art["size_in_bytes"] / 1048576))

    blob = get(art["archive_download_url"], tok)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        apks = [n for n in z.namelist() if n.lower().endswith(".apk")]
        if not apks:
            die("no .apk inside the artifact: %s" % z.namelist()[:6])
        # The largest, in case a future build ever splits per ABI and the zip
        # holds several: the fat one is the safe thing to serve.
        name = max(apks, key=lambda n: z.getinfo(n).file_size)
        data = z.read(name)
    print("  took %s, %.1f MB" % (name, len(data) / 1048576))

    # IS IT AN APK? A zip whose first bytes are not PK is a redirect page or
    # an error body, and writing that over the served build would take every
    # phone's update down with a file that installs as nothing.
    if data[:2] != b"PK":
        die("that is not a zip/apk — first bytes %s" % data[:8].hex())
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        inner = z.namelist()
        if "AndroidManifest.xml" not in inner:
            die("no AndroidManifest.xml inside — not an APK")
        # AND IS IT THE VERSION CLAIMED? The manifest is binary XML with the
        # version name stored as UTF-16, so search for it as bytes. This is
        # the check that would have caught 1.74.0 shipping labelled 1.73.0.
        manifest = z.read("AndroidManifest.xml")
    if version.encode("utf-16-le") not in manifest:
        die("the APK's manifest does not contain %r — it was probably built "
            "before VERSION was bumped" % version)
    print("  manifest carries %s" % version)

    out_dir = BACKEND.parent / "mobile"
    out_dir.mkdir(exist_ok=True)
    apk_path = out_dir / "app-release.apk"
    meta_path = out_dir / "android.json"

    was = {}
    if meta_path.is_file():
        try:
            was = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    print("  serving now: %s -> publishing %s" % (was.get("version", "nothing"),
                                                  version))
    if args.dry_run:
        print("dry run, nothing written")
        return

    # Written to a temporary file and moved into place, so a phone asking for
    # the APK mid-write gets the old one whole rather than half the new one.
    tmp = tempfile.NamedTemporaryFile(dir=out_dir, delete=False, suffix=".part")
    try:
        tmp.write(data)
        tmp.close()
        shutil.move(tmp.name, apk_path)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)

    meta = {
        "version": version,
        "build": build,
        "notes": args.notes or was.get("notes", ""),
        "filename": "app-release.apk",
    }
    meta_tmp = meta_path.with_suffix(".json.part")
    meta_tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    shutil.move(str(meta_tmp), str(meta_path))

    print()
    print("published %s (build %s), %.1f MB" % (version, build,
                                                len(data) / 1048576))
    print("  " + str(apk_path))
    print("no restart needed — mobile.py reads this folder per request")


if __name__ == "__main__":
    main()
