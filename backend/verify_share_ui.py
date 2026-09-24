"""The Share button, driven in a real browser.

WHAT CAN AND CANNOT BE PROVED HERE. Headless Chrome has no system share sheet,
so `navigator.share` is absent and this exercises the FALLBACK — the file is
fetched and saved instead. That is the path that matters most to get right,
because it is the one that fails silently: a Share button that quietly does
nothing is indistinguishable from a broken one.

The share-sheet path is then proved by installing a fake `navigator.share` and
checking what the app HANDS it: the right number of files, real bytes, and
sensible names. What cannot be checked anywhere but a phone is whether the
sheet itself lists WhatsApp — that is the operating system's business, and
saying so is more honest than a green tick that means nothing.

Against the THROWAWAY instance on 8099.
"""
import asyncio
import io
import json
import subprocess
import sys
import time
import urllib.request
import uuid

import websockets
from PIL import Image

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9226
BASE = "http://127.0.0.1:8099"
EMAIL, PASSWORD = "priya@example.com", "DemoHouse#2026"
PROFILE_DIR = r"C:\Users\Pro-TEAM\AppData\Local\Temp\claude\cdp-share"

FAIL = []


def check(ok, label, extra=""):
    line = "  %-56s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))
    if not ok:
        FAIL.append(label)


def call(path, body=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + tok} if tok else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.load(r)


def seed(tok):
    """Two photos, so multi-select share has something to send."""
    st, d = call("/api/gallery?limit=5", tok=tok)
    if len(d.get("items") or []) >= 2:
        return
    for i in range(2):
        im = Image.new("RGB", (240, 180), (40 + i * 60, 90, 200))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        b = "----sn" + uuid.uuid4().hex
        out = bytearray()
        out += ('--%s\r\nContent-Disposition: form-data; name="file";'
                ' filename="share-%s.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
                % (b, uuid.uuid4().hex[:5])).encode()
        out += buf.getvalue() + ("\r\n--%s--\r\n" % b).encode()
        req = urllib.request.Request(
            BASE + "/api/gallery/upload", data=bytes(out), method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=" + b,
                     "Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()


def target_ws():
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as r:
                for t in json.load(r):
                    if t.get("type") == "page":
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("chrome never exposed a debugging target")


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.events = ws, 0, []

    async def send(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if "id" not in msg:
                self.events.append(msg)
                continue
            if msg["id"] == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr, wait=True):
        r = await self.send("Runtime.evaluate", expression=expr,
                            awaitPromise=wait, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")


# The fake sheet. Installed before the app runs, so the app's own capability
# probe sees it exactly as it would see a real one.
FAKE_SHARE = """
(() => {
  window.__shared = [];
  navigator.share = async (data) => {
    window.__shared.push({
      files: (data.files || []).map(f => ({ name: f.name, type: f.type, size: f.size })),
      title: data.title || null,
    });
  };
  navigator.canShare = (data) => !!(data && data.files && data.files.length);
  window.__downloads = [];
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (this.download) { window.__downloads.push(this.download); return; }
    return click.apply(this, arguments);
  };
  return 'installed';
})()
"""

NO_SHARE = """
(() => {
  delete navigator.share;
  delete navigator.canShare;
  window.__downloads = [];
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (this.download) { window.__downloads.push(this.download); return; }
    return click.apply(this, arguments);
  };
  return 'removed';
})()
"""


async def open_app(c, token, script):
    await c.send("Page.navigate", url=BASE + "/")
    await asyncio.sleep(2)
    await c.eval(
        "(() => {const t=%s;"
        "['auth.token','token','authToken','finmate.token','app.token']"
        ".forEach(k=>localStorage.setItem(k,t)); return 1})()" % json.dumps(token),
        wait=False)
    # Installed on EVERY document, so it is there before the app's module code
    # runs its capability probe.
    await c.send("Page.addScriptToEvaluateOnNewDocument", source=script)
    await c.send("Page.navigate", url=BASE + "/")
    await asyncio.sleep(4)
    await c.eval(
        "[...document.querySelectorAll('button, a, [role=button]')]"
        ".find(el => /gallery|photos/i.test((el.textContent||'').trim())"
        "         && el.offsetParent)?.click()", wait=False)
    await asyncio.sleep(3)


async def main():
    st, d = call("/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    token = d["token"]
    seed(token)

    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
         "--no-default-browser-check", "--disable-gpu",
         "--window-size=1280,1600", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = target_ws()
        async with websockets.connect(url, max_size=60_000_000) as ws:
            c = CDP(ws)
            await c.send("Runtime.enable")
            await c.send("Page.enable")
            await c.send("Log.enable")

            # ---- with a share sheet ------------------------------------
            print("\n  --- a device WITH a share sheet ---")
            await open_app(c, token, FAKE_SHARE)
            opened = await c.eval("""
            (async () => {
              const tile = document.querySelector('.ptl-tile .ptl-hit');
              if (!tile) return 'no photos';
              tile.click();
              await new Promise(r => setTimeout(r, 1800));
              return document.querySelector('.viewer') ? 'open' : 'no viewer';
            })()
            """)
            check(opened == "open", "a photo opens", opened)

            has = await c.eval(
                "[...document.querySelectorAll('.viewer-btn')]"
                ".some(b => b.getAttribute('aria-label') === 'Share')", wait=False)
            check(has is True, "the viewer offers Share", has)

            got = await c.eval("""
            (async () => {
              [...document.querySelectorAll('.viewer-btn')]
                .find(b => b.getAttribute('aria-label') === 'Share').click();
              await new Promise(r => setTimeout(r, 2500));
              return JSON.stringify({shared: window.__shared, dl: window.__downloads});
            })()
            """)
            r = json.loads(got)
            check(len(r["shared"]) == 1, "pressing it opens the share sheet", r)
            if r["shared"]:
                f = r["shared"][0]["files"][0]
                # The bytes have to be REAL. A zero-length file reaches the
                # sheet, shows an icon, and sends nothing.
                check(f["size"] > 500, "with the real photo in it, not an empty file",
                      f["size"])
                # The NAME and the TYPE have to agree. A moving highlight is
                # stored as an animated WebP, and the caller guesses ".jpg"
                # from "it is a photo" — so the sheet was handed WebP called
                # .jpg, which some apps refuse and others pass on for the
                # recipient's phone to refuse.
                ext = f["name"].rsplit(".", 1)[-1].lower()
                want = {"image/jpeg": "jpg", "image/webp": "webp",
                        "image/png": "png", "video/mp4": "mp4"}.get(f["type"], ext)
                check(ext == want, "the filename agrees with the bytes",
                      (f["name"], f["type"]))
                # Image OR video: the timeline holds both, and which one is
                # first depends on what the other tests left. Asserting
                # "image" made a correct share of a video look wrong.
                check(f["type"].startswith(("image/", "video/")),
                      "typed as the medium it is", f["type"])
            check(not r["dl"], "and nothing was downloaded behind its back", r["dl"])

            # ---- without one -------------------------------------------
            print("\n  --- a device WITHOUT one (desktop) ---")
            await open_app(c, token, NO_SHARE)
            got = await c.eval("""
            (async () => {
              const tile = document.querySelector('.ptl-tile .ptl-hit');
              tile.click();
              await new Promise(r => setTimeout(r, 1800));
              [...document.querySelectorAll('.viewer-btn')]
                .find(b => b.getAttribute('aria-label') === 'Share').click();
              // Watched for rather than sampled once: the toast appears when
              // the fetch finishes and disappears again on its own, so a
              // single look 2.5s later can easily fall after it has gone.
              let toast = false;
              for (let i = 0; i < 40; i++) {
                await new Promise(r => setTimeout(r, 150));
                const t = document.querySelector('.toast');
                if (t && /saved to your device/i.test(t.textContent || '')) {
                  toast = true; break;
                }
              }
              return JSON.stringify({dl: window.__downloads, toast});
            })()
            """)
            r = json.loads(got)
            # THE ONE THAT MATTERS. With no sheet the button must still DO
            # something — a Share that silently does nothing is the failure
            # nobody reports because it looks like a slow network.
            check(len(r["dl"]) == 1, "Share still saves the file", r["dl"])
            check(r["toast"], "and says so, so it does not look like nothing happened",
                  r["toast"])

            # ---- several at once ---------------------------------------
            print("\n  --- several photos at once ---")
            await open_app(c, token, FAKE_SHARE)
            got = await c.eval("""
            (async () => {
              const checks = [...document.querySelectorAll('.ptl-check')]
                .filter(c => !c.classList.contains('head'));
              if (checks.length < 2) return JSON.stringify({skip: checks.length});
              checks[0].click();
              await new Promise(r => setTimeout(r, 400));
              checks[1].click();
              await new Promise(r => setTimeout(r, 600));
              const bar = document.querySelector('.selbar');
              const btn = bar && [...bar.querySelectorAll('.selbar-act')]
                .find(b => b.title === 'Share');
              if (!btn) return JSON.stringify({nobutton: true});
              btn.click();
              await new Promise(r => setTimeout(r, 3500));
              return JSON.stringify({shared: window.__shared});
            })()
            """)
            r = json.loads(got)
            if r.get("skip") is not None:
                check(False, "two photos to select", r)
            elif r.get("nobutton"):
                check(False, "the selection bar offers Share", r)
            else:
                check(len(r["shared"]) == 1, "one sheet, not one per photo", r)
                files = r["shared"][0]["files"] if r["shared"] else []
                check(len(files) == 2, "carrying BOTH photos", len(files))
                check(all(f["size"] > 500 for f in files),
                      "both with real bytes", [f["size"] for f in files])
                check(len({f["name"] for f in files}) == len(files),
                      "and distinct names, or the sheet keeps one", files)

            errs = [e for e in c.events
                    if e.get("method") == "Log.entryAdded"
                    and e["params"]["entry"].get("level") == "error"]

            def noise(e):
                t = e["params"]["entry"].get("text", "")
                u = e["params"]["entry"].get("url") or ""
                return "404" in t or "/api/update" in u
            real = [e for e in errs if not noise(e)]
            check(not real, "no console errors",
                  [e["params"]["entry"]["text"][:90] for e in real][:3])
    finally:
        proc.terminate()

    print()
    print("  NOT CHECKED HERE: whether the sheet actually lists WhatsApp.")
    print("  That is the operating system's business and only a phone can say.")
    print()
    if FAIL:
        print("  %d FAILED:" % len(FAIL))
        for f in FAIL:
            print("    -", f)
        raise SystemExit(1)
    print("  ALL PASS")


asyncio.run(main())
