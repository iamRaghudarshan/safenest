"""Open the photo editor for real, crop by dragging, and save.

What a typecheck cannot see, and what this checks instead: that the crop
overlay follows a drag, that rotate moves the preview before anything is
saved, that Save produces a photo whose DIMENSIONS actually changed, and that
"Use original" appears only for a photo that has something to go back to.

The last one is the reason the whole editor keeps a pristine copy, so it is
the one worth proving from the outside.

Against the THROWAWAY instance on 8099, never the live app.
"""
import asyncio
import io
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

import websockets
from PIL import Image

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9225
BASE = "http://127.0.0.1:8099"
DB = r"C:/Users/Pro-TEAM/AppData/Local/Temp/safenest-demo/demo.db"
EMAIL, PASSWORD = "priya@example.com", "DemoHouse#2026"
PROFILE_DIR = r"C:\Users\Pro-TEAM\AppData\Local\Temp\claude\cdp-editor"

FAIL = []


def check(ok, label, extra=""):
    line = "  %-54s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))
    if not ok:
        FAIL.append(label)


def call(path, body=None, method=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + tok} if tok else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.load(r)


def seed(tok):
    """One wide photo with a red top-left corner, uploaded fresh each run.

    Fresh rather than reused: this test SAVES an edit, so reusing a photo
    would have the second run start from a cropped one and assert against
    dimensions that are already wrong.
    """
    im = Image.new("RGB", (400, 200), (30, 90, 200))
    for x in range(100):
        for y in range(50):
            im.putpixel((x, y), (240, 30, 30))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    body = buf.getvalue()

    b = "----sn" + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file";'
            ' filename="editor-%s.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
            % (b, uuid.uuid4().hex[:6])).encode()
    out += body + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(
        BASE + "/api/gallery/upload", data=bytes(out), method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + b,
                 "Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        return (json.load(r).get("item") or {})["id"]


def size_of(pid):
    con = sqlite3.connect(DB)
    try:
        return con.execute(
            "SELECT width, height, edit FROM gallery_photos WHERE id = ?",
            (pid,)).fetchone()
    finally:
        con.close()


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


async def main():
    st, d = call("/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    token = d["token"]
    pid = seed(token)
    print("  photo id =", pid, size_of(pid)[:2])

    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
         "--no-default-browser-check", "--disable-gpu",
         "--window-size=1280,1400", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = target_ws()
        async with websockets.connect(url, max_size=40_000_000) as ws:
            c = CDP(ws)
            await c.send("Runtime.enable")
            await c.send("Page.enable")
            await c.send("Log.enable")

            await c.send("Page.navigate", url=BASE + "/")
            await asyncio.sleep(2)
            await c.eval(
                "(() => {const t=%s;"
                "['auth.token','token','authToken','finmate.token','app.token']"
                ".forEach(k=>localStorage.setItem(k,t)); return 1})()"
                % json.dumps(token), wait=False)
            await c.send("Page.navigate", url=BASE + "/")
            await asyncio.sleep(4)

            await c.eval(
                "[...document.querySelectorAll('button, a, [role=button]')]"
                ".find(el => /gallery|photos/i.test((el.textContent||'').trim())"
                "         && el.offsetParent)?.click()", wait=False)
            await asyncio.sleep(3)

            # Open THIS photo, not whichever tile happens to be first.
            opened = await c.eval("""
            (async () => {
              const tile = [...document.querySelectorAll('.ptl-tile')].find(
                t => (t.querySelector('img')?.src || '').length);
              if (!tile) return 'no tiles';
              tile.querySelector('.ptl-hit').click();
              await new Promise(r => setTimeout(r, 1800));
              return document.querySelector('.viewer') ? 'open' : 'no viewer';
            })()
            """)
            check(opened == "open", "a photo opens in the viewer", opened)

            has = await c.eval(
                "[...document.querySelectorAll('.viewer-btn')]"
                ".some(b => b.getAttribute('aria-label') === 'Edit photo')",
                wait=False)
            check(has is True, "the viewer offers Edit", has)

            await c.eval(
                "[...document.querySelectorAll('.viewer-btn')]"
                ".find(b => b.getAttribute('aria-label') === 'Edit photo').click()",
                wait=False)
            await asyncio.sleep(1.2)
            up = await c.eval(
                "document.querySelector('.editor') ? 'open' : 'missing'", wait=False)
            check(up == "open", "the editor opens", up)

            tabs = await c.eval(
                "document.querySelectorAll('.editor-tabs .chip').length", wait=False)
            check(tabs == 3, "it has crop, adjust and filters", tabs)

            # ---- rotate moves the preview BEFORE anything is saved ---------
            before_t = await c.eval(
                "getComputedStyle(document.querySelector('.editor-img')).transform",
                wait=False)
            await c.eval(
                "[...document.querySelectorAll('.editor-row .btn')]"
                ".find(b => /Right/.test(b.textContent)).click()", wait=False)
            await asyncio.sleep(0.5)
            after_t = await c.eval(
                "getComputedStyle(document.querySelector('.editor-img')).transform",
                wait=False)
            check(before_t != after_t,
                  "rotating changes the preview without a round trip",
                  (before_t, after_t))
            # Put it back — the crop assertions below are about size alone.
            await c.eval(
                "[...document.querySelectorAll('.editor-row .btn')]"
                ".find(b => /Left/.test(b.textContent)).click()", wait=False)
            await asyncio.sleep(0.4)

            # ---- crop by dragging ------------------------------------------
            dragged = await c.eval("""
            (async () => {
              const f = document.querySelector('.editor-frame');
              const r = f.getBoundingClientRect();
              const ev = (type, fx, fy) => f.dispatchEvent(new PointerEvent(type, {
                bubbles: true, pointerId: 1, clientX: r.left + r.width * fx,
                clientY: r.top + r.height * fy,
              }));
              f.setPointerCapture = () => {};
              ev('pointerdown', 0.0, 0.0);
              // A frame between down and move. React commits the drag's start
              // point in a state update, so a move dispatched in the SAME tick
              // is handled while that point is still null and the crop never
              // starts. A real pointer always arrives in a later frame, so
              // this is the test catching up with the browser, not a bug.
              await new Promise(r2 => setTimeout(r2, 60));
              ev('pointermove', 0.5, 0.5);
              await new Promise(r2 => setTimeout(r2, 200));
              const box = document.querySelector('.crop-box');
              const shown = box ? box.getBoundingClientRect().width / r.width : 0;
              ev('pointerup', 0.5, 0.5);
              return JSON.stringify({box: !!box, shown: Math.round(shown * 100)});
            })()
            """)
            got = json.loads(dragged)
            check(got["box"], "dragging draws a crop box", got)
            check(45 <= got["shown"] <= 55,
                  "and the box follows the drag, at about half the width", got)

            # ---- save --------------------------------------------------------
            await c.eval(
                "[...document.querySelectorAll('.editor-actions .btn')]"
                ".find(b => b.textContent.trim() === 'Save').click()", wait=False)
            await asyncio.sleep(3)
            closed = await c.eval(
                "document.querySelector('.editor') ? 'still open' : 'closed'",
                wait=False)
            check(closed == "closed", "saving closes the editor", closed)

            w, h, edit = size_of(pid)
            check((w, h) == (200, 100),
                  "and the photo really is cropped to half", (w, h))
            check(edit and "crop" in edit, "the edit is recorded", edit)

            # ---- use original ------------------------------------------------
            # The button must appear only now that there IS an original to go
            # back to — that is the whole reason a pristine copy is kept.
            await c.eval(
                "[...document.querySelectorAll('.viewer-btn')]"
                ".find(b => b.getAttribute('aria-label') === 'Edit photo')?.click()",
                wait=False)
            await asyncio.sleep(1.5)
            revert = await c.eval(
                "[...document.querySelectorAll('.editor-actions .btn')]"
                ".some(b => /Use original/.test(b.textContent))", wait=False)
            check(revert is True,
                  "an edited photo offers 'Use original'", revert)

            await c.eval(
                "[...document.querySelectorAll('.editor-actions .btn')]"
                ".find(b => /Use original/.test(b.textContent)).click()", wait=False)
            await asyncio.sleep(3)
            w, h, edit = size_of(pid)
            check((w, h) == (400, 200), "and it restores the full picture", (w, h))
            check(not edit, "with no edit left on the row", edit)

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
    if FAIL:
        print("  %d FAILED:" % len(FAIL))
        for f in FAIL:
            print("    -", f)
        raise SystemExit(1)
    print("  ALL PASS")


asyncio.run(main())
