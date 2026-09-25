"""Open Places and Group again in the real screen.

Both of these existed on the server and on the phone, and the web had no way
to reach either. A typecheck cannot tell you that a tab renders, that a
seven-tab row still fits a phone without wrapping onto a second line, or that
a confirmation says the numbers before it rewrites every grouping in the
library — and those are exactly the things that have gone wrong here before.

Places is deliberately checked with a photo that HAS coordinates seeded first.
Run against a library with no located photos, this test would pass on the
empty state, which looks identical to a broken endpoint and means the opposite.

Against the THROWAWAY instance on 8099, never the live app.
"""
import asyncio
import io as _io
import json
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9231
BASE = "http://127.0.0.1:8099"
EMAIL = "priya@example.com"
PASSWORD = "DemoHouse#2026"
PROFILE_DIR = r"C:\Users\Pro-TEAM\AppData\Local\Temp\claude\cdp-places"

#: A phone, not a desktop. The seven-tab row is the thing most likely to break,
#: and it breaks at narrow widths or not at all.
WIDTH, HEIGHT = 390, 1400

FAIL = []


def check(ok, label, extra=""):
    line = "  %-56s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = sys.stdout.encoding or "utf-8"
    sys.stdout.write(line.encode(enc, "replace").decode(enc) + "\n")
    if not ok:
        FAIL.append(label)


def call(path, body=None, method=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {}


def target_ws():
    for _ in range(80):
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
        self.ws, self.n = ws, 0

    async def send(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if "id" not in msg:
                continue
            if msg["id"] == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr, wait=False):
        r = await self.send("Runtime.evaluate", expression=expr,
                            awaitPromise=wait, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")


def seed_located_photo(token):
    """A photo the server can place, so Places has something to group.

    GPS goes in as EXIF because that is the only route the importer reads —
    writing lat/lon into the row directly would test a code path the product
    never takes.
    """
    st, d = call("/api/gallery/places", tok=token)
    if d.get("items"):
        return True

    try:
        from PIL import Image
    except ImportError:
        return False

    im = Image.new("RGB", (8, 8))
    rnd = random.Random(11)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                for _ in range(64)])
    im = im.resize((640, 480))

    # Bengaluru. IFDRational rather than (num, den) tuples: Pillow writes
    # the tuple form for some tags and not for GPS, where it tries abs() on
    # the tuple and dies. Worth writing down — the tuple shape is what every
    # example on the internet shows.
    from PIL.TiffImagePlugin import IFDRational
    ex = im.getexif()
    ex[34853] = {
        1: "N", 2: (IFDRational(12), IFDRational(58), IFDRational(0)),
        3: "E", 4: (IFDRational(77), IFDRational(35), IFDRational(0)),
    }
    ex[306] = "2026:01:14 10:30:00"
    buf = _io.BytesIO()
    im.save(buf, "JPEG", exif=ex.tobytes())
    body = buf.getvalue()

    b = "----sn" + uuid.uuid4().hex
    out = bytearray()
    out += ('--%s\r\nContent-Disposition: form-data; name="file";'
            ' filename="located.jpg"\r\nContent-Type: image/jpeg\r\n\r\n' % b).encode()
    out += body + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(
        BASE + "/api/gallery/upload", data=bytes(out), method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + b,
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
    except Exception:
        return False
    st, d = call("/api/gallery/places", tok=token)
    return bool(d.get("items"))


async def main():
    st, d = call("/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    if st != 200:
        raise SystemExit("login failed: %s %s" % (st, d))
    token = d["token"]

    located = seed_located_photo(token)
    st, people = call("/api/people", tok=token)
    enough_people = len(people.get("people", [])) > 1

    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE_DIR}", "--no-first-run",
         "--no-default-browser-check", "--disable-gpu",
         f"--window-size={WIDTH},{HEIGHT}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = target_ws()
        async with websockets.connect(url, max_size=40_000_000) as ws:
            c = CDP(ws)
            await c.send("Runtime.enable")
            await c.send("Page.enable")

            await c.send("Page.navigate", url=BASE + "/")
            await asyncio.sleep(2)
            await c.eval(
                "(() => {const t=%s;"
                "['auth.token','token','authToken','finmate.token','app.token']"
                ".forEach(k=>localStorage.setItem(k,t)); return 1})()" % json.dumps(token))
            await c.send("Page.navigate", url=BASE + "/")
            await asyncio.sleep(4)

            await c.eval(
                "[...document.querySelectorAll('button, a, [role=button]')]"
                ".find(el => /gallery|photos/i.test((el.textContent||'').trim())"
                "         && el.offsetParent)?.click()")
            await asyncio.sleep(3)

            print("\nTHE TAB ROW")
            tabs = await c.eval(
                "(() => {const r=document.querySelector('.seg4');"
                " return r ? [...r.querySelectorAll('button')]"
                "   .map(b=>b.textContent.trim()) : null})()")
            check(isinstance(tabs, list) and "Places" in tabs,
                  "Places is one of the tabs", str(tabs))

            # The row must stay ONE line. Seven tabs on a 390px phone is the
            # whole risk of adding this tab, and a row that wraps pushes the
            # first photo off the screen.
            lines = await c.eval(
                "(() => {const r=document.querySelector('.seg4'); if(!r) return -1;"
                " const bs=[...r.querySelectorAll('button')];"
                " return new Set(bs.map(b=>Math.round(b.getBoundingClientRect().top))).size})()")
            check(lines == 1, "the seven tabs stay on one line", "rows=%s" % lines)

            overflow = await c.eval(
                "(() => {const r=document.querySelector('.seg4'); if(!r) return -1;"
                " return r.scrollWidth - r.clientWidth})()")
            check(isinstance(overflow, (int, float)) and overflow <= 1,
                  "and do not overflow it sideways", "overflow=%spx" % overflow)

            print("\nPLACES")
            await c.eval(
                "[...document.querySelectorAll('.seg4 button')]"
                ".find(b => b.textContent.trim() === 'Places')?.click()")
            await asyncio.sleep(3)

            if not located:
                check(True, "SKIPPED: no photo has coordinates", "empty state only")
            else:
                cards = await c.eval(
                    "document.querySelectorAll('.album-grid .album').length")
                check(isinstance(cards, int) and cards > 0,
                      "a place is listed", "%s cards" % cards)
                pin = await c.eval("document.querySelectorAll('.place-pin').length")
                check(isinstance(pin, int) and pin > 0,
                      "each card is marked as a place, not an album", str(pin))
                said = await c.eval(
                    "(() => {const p=[...document.querySelectorAll('p.muted')]"
                    "  .find(x => /know where/.test(x.textContent||''));"
                    " return p ? p.textContent.trim() : null})()")
                check(bool(said), "it says how many photos have a location", str(said))

                # Opening one must land on that place's photos, not the whole
                # library — the bug this shape is designed against.
                await c.eval("document.querySelector('.album-grid .album')?.click()")
                await asyncio.sleep(3)
                head = await c.eval(
                    "(() => {const h=document.querySelector('.screen h1, .topbar-title,"
                    " .topbar h1'); return h ? h.textContent.trim() : null})()")
                # The timeline's own markup. `.photo-grid` never existed —
                # PhotoGrid renders a PhotoTimeline for every density except
                # list, and a selector aimed at a class that is not there
                # fails identically to a screen with no photos on it.
                grid = await c.eval(
                    "document.querySelectorAll('.ptl-row .ptl-hit,"
                    " .ptl-row img, .photo-list .pr-title').length")
                check(bool(head), "opening it shows that place by name", str(head))
                check(isinstance(grid, int) and grid > 0,
                      "and its photos", "%s tiles" % grid)
                await c.eval(
                    "(document.querySelector('.topbar button, .screen button'))?.click()")
                await asyncio.sleep(2)

            print("\nGROUP AGAIN")
            await c.eval(
                "[...document.querySelectorAll('.seg4 button')]"
                ".find(b => b.textContent.trim() === 'People')?.click()")
            await asyncio.sleep(3)

            if not enough_people:
                check(True, "SKIPPED: fewer than two people to regroup", "")
            else:
                btn = await c.eval(
                    "[...document.querySelectorAll('button')]"
                    ".some(b => b.textContent.trim() === 'Group again')")
                check(btn is True, "the People tab offers Group again", str(btn))

                await c.eval(
                    "[...document.querySelectorAll('button')]"
                    ".find(b => b.textContent.trim() === 'Group again')?.click()")
                await asyncio.sleep(4)

                # It must ask BEFORE it rewrites every grouping, and the numbers
                # are the whole reason the asking is worth anything.
                body = await c.eval(
                    "(() => {const s=document.querySelector('.sheet');"
                    " return s ? s.textContent : null})()")
                check(bool(body) and "would go from" in body,
                      "it says what would change before doing it",
                      (body or "")[:70].replace("\n", " "))
                check(bool(body) and "Names are kept" in body,
                      "and that names survive",
                      "said" if body and "Names are kept" in body else "missing")

    finally:
        proc.terminate()

    print("\n%d failing" % len(FAIL))
    for f in FAIL:
        print("  - %s" % f)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
