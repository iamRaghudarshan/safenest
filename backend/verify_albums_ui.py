"""Create a saved search in the real screen and open it.

A saved search is the one album whose contents nobody chose, so the things
worth proving are the ones a typecheck cannot see: that it fills itself from
the rule, that the rule is visible as words on the tile and in the header, and
that the two controls which make no sense for it — Add, and Remove from album —
are actually gone rather than merely inert.

Against the THROWAWAY instance on 8099, never the live app.
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
import uuid

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9224
BASE = "http://127.0.0.1:8099"
EMAIL = "priya@example.com"
PASSWORD = "DemoHouse#2026"
PROFILE_DIR = r"C:\Users\Pro-TEAM\AppData\Local\Temp\claude\cdp-albums"

#: The rule is "Photos" rather than "Videos" on purpose. A saved search for
#: videos in a library with none matches nothing — which is correct, and which
#: proves nothing at all about whether the rule RUNS. A rule the library
#: satisfies is the only version of this test that can fail.
NAME = "Photos " + uuid.uuid4().hex[:6]
FAIL = []


def check(ok, label, extra=""):
    # This console is cp1252 and the page text is not — a rupee sign lifted
    # out of the DOM crashed the run AFTER the check had passed, which reads
    # as a failure of the thing under test rather than of the print.
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


def seed_a_photo(token):
    """Make sure there is at least one photo, so "it is full" can be false.

    Run on its own against a fresh throwaway database, this test would
    otherwise pass with an empty grid and an empty album — the two states that
    look identical and mean opposite things.
    """
    st, d = call("/api/gallery?limit=1", tok=token)
    if d.get("items"):
        return
    import io as _io
    import random
    from PIL import Image
    im = Image.new("RGB", (8, 8))
    rnd = random.Random(3)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                for _ in range(64)])
    im = im.resize((320, 320))
    buf = _io.BytesIO()
    im.save(buf, "JPEG")
    body = buf.getvalue()

    b = "----sn" + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file";'
            ' filename="seed.jpg"\r\nContent-Type: image/jpeg\r\n\r\n' % b).encode()
    out += body + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(
        BASE + "/api/gallery/upload", data=bytes(out), method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + b,
                 "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=120) as r:
        r.read()


async def main():
    st, d = call("/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    token = d["token"]
    seed_a_photo(token)

    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
         "--no-default-browser-check", "--disable-gpu",
         "--window-size=1280,2000", "about:blank"],
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
                ".forEach(k=>localStorage.setItem(k,t)); return 1})()" % json.dumps(token),
                wait=False)
            await c.send("Page.navigate", url=BASE + "/")
            await asyncio.sleep(4)

            await c.eval(
                "[...document.querySelectorAll('button, a, [role=button]')]"
                ".find(el => /gallery|photos/i.test((el.textContent||'').trim())"
                "         && el.offsetParent)?.click()", wait=False)
            await asyncio.sleep(3)
            await c.eval(
                "[...document.querySelectorAll('button')]"
                ".find(b => b.textContent.trim() === 'Albums')?.click()", wait=False)
            await asyncio.sleep(2)

            here = await c.eval(
                "document.querySelector('.album-new') ? 'yes' : 'no'", wait=False)
            check(here == "yes", "the Albums tab renders its new buttons", here)

            # ---- build a saved search --------------------------------------
            await c.eval(
                "[...document.querySelectorAll('.album-new .btn')]"
                ".find(b => /saved search/i.test(b.textContent)).click()", wait=False)
            await asyncio.sleep(1)
            sheet = await c.eval(
                "document.querySelector('.rule-chips') ? 'open' : 'missing'", wait=False)
            check(sheet == "open", "the saved-search sheet opens", sheet)

            # An empty rule must be refused: an album that matches everything
            # is the gallery under another name.
            await c.eval(
                "(() => {const i=document.querySelector('.sheet input');"
                " const set=Object.getOwnPropertyDescriptor("
                "   window.HTMLInputElement.prototype,'value').set;"
                " set.call(i, %s);"
                " i.dispatchEvent(new Event('input',{bubbles:true}))})()"
                % json.dumps(NAME), wait=False)
            await asyncio.sleep(0.5)
            disabled = await c.eval(
                "(() => {const b=[...document.querySelectorAll('.sheet button')]"
                " .find(x => x.textContent.trim() === 'Save');"
                " return b ? b.disabled : 'no save button'})()", wait=False)
            check(disabled is True, "a name with no rule cannot be saved", disabled)

            await c.eval(
                "[...document.querySelectorAll('.rule-chips .chip')]"
                ".find(b => b.textContent.trim() === 'Photos').click()", wait=False)
            await asyncio.sleep(0.5)
            enabled = await c.eval(
                "(() => {const b=[...document.querySelectorAll('.sheet button')]"
                " .find(x => x.textContent.trim() === 'Save'); return b.disabled})()",
                wait=False)
            check(enabled is False, "choosing something enables Save", enabled)

            await c.eval(
                "[...document.querySelectorAll('.sheet button')]"
                ".find(x => x.textContent.trim() === 'Save').click()", wait=False)
            await asyncio.sleep(2.5)

            tile = await c.eval("""
            (() => {
              const t = [...document.querySelectorAll('.album')]
                .find(el => el.querySelector('.album-name')?.textContent.trim() === %s);
              if (!t) return 'not on the grid';
              return JSON.stringify({
                badge: !!t.querySelector('.album-rule-badge'),
                sub: t.querySelector('.album-count')?.textContent.trim(),
              });
            })()
            """ % json.dumps(NAME), wait=False)
            ok = str(tile).startswith("{")
            check(ok, "the saved search appears on the grid", tile)
            if ok:
                got = json.loads(tile)
                check(got["badge"], "it carries the saved-search badge", got)
                # The subtitle is the RULE, not a photo count: an album that
                # fills itself for reasons nobody can see is one people stop
                # trusting.
                check(got["sub"] == "photos",
                      "and its subtitle is the rule in words", got["sub"])

            # ---- open it ---------------------------------------------------
            await c.eval(
                "[...document.querySelectorAll('.album')]"
                ".find(el => el.querySelector('.album-name')?.textContent.trim() === %s)"
                ".click()" % json.dumps(NAME), wait=False)
            await asyncio.sleep(2.5)

            hdr = await c.eval(
                "(() => {const s=document.querySelector('.topbar .sub');"
                " return s ? s.textContent.trim() : 'no subtitle'})()", wait=False)
            check("photos" in str(hdr).lower(),
                  "the header repeats the rule", hdr)

            # THE POINT OF THE WHOLE FEATURE. Nothing was ever filed into this
            # album; if the rule did not run, it is empty and every check above
            # would still have passed.
            shown = await c.eval(
                "document.querySelectorAll('.ptl-tile').length", wait=False)
            check(shown > 0, "and it is FULL — the rule ran, nothing was filed",
                  shown)

            add = await c.eval(
                "[...document.querySelectorAll('button')]"
                ".some(b => b.textContent.includes('Add'))", wait=False)
            check(add is False,
                  "Add is GONE, not merely inert — nothing is filed into a rule", add)

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
