"""Open the Documents screen for real and use the new controls.

Against the THROWAWAY instance on 8099, never the live app. It serves the same
built SPA from frontend/dist, so this is the real screen — it just reads the
throwaway database.

Proves the things a typecheck cannot: that the selection bar appears when a
card is picked, that the filter panel opens and narrows the list, that rename
is reachable, and that nothing throws into the console while doing it.
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
PORT = 9223
BASE = "http://127.0.0.1:8099"
EMAIL = "priya@example.com"
PASSWORD = "DemoHouse#2026"
PROFILE_DIR = r"C:\Users\Pro-TEAM\AppData\Local\Temp\claude\cdp-docs"

FAIL = []

#: Unique per run. A fixed name works once and then 409s on the folder's own
#: unique index, which shows up as a console error rather than as the setup
#: failure it really is.
FOLDER = "Statements " + uuid.uuid4().hex[:6]

#: A title of this test's own, not "Budget". Two OTHER scripts upload a file
#: called Budget with different contents, so which one this test opened
#: depended on which of them ran last — and the CSV assertions then failed
#: against a file that was never meant to satisfy them.
CSV_TITLE = "Preview fixture"


def check(ok, label, extra=""):
    # This console is cp1252 and the page text is not — a rupee sign lifted
    # out of the DOM crashed the run AFTER the check had passed, which reads
    # as a failure of the thing under test rather than of the print.
    line = "  %-52s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))
    if not ok:
        FAIL.append(label)


def login_token():
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"email": EMAIL, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["token"]


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


CLICK = """
(() => {
  const hit = [...document.querySelectorAll(%s)]
      .find(el => %s);
  if (!hit) return 'not found';
  hit.click();
  return 'clicked';
})()
"""


def seed(token):
    """Upload the fixtures this test needs, rather than hoping they are there.

    It used to run against whatever the previous script had left in the
    database, which meant it passed all afternoon and then failed with
    "Cannot read properties of undefined" the moment another test ran first.
    A test that depends on another test's leftovers is not a test; it is a
    coincidence that has not broken yet.

    Everything here is required by a specific check below: two PDFs so the
    type filter can narrow to something, a fourth file so the filter narrowing
    is visible, and the CSV with a quoted comma for the preview.
    """
    want = [
        ("Rent agreement", "rent.pdf", b"%PDF-1.4\nrent\n%%EOF\n"),
        ("Salary slip", "slip.pdf", b"%PDF-1.4\nslip\n%%EOF\n"),
        ("Notes", "notes.txt", b"just some notes\n"),
        (CSV_TITLE, "preview.csv",
         b'Item,Amount,Note\nRent,18000,monthly\n'
         b'"Sharma, Priya",2500,"quoted, comma"\n'),
    ]
    # ?folder=0 is the TOP LEVEL, not "everywhere". Asking without it returns
    # the whole tree, so a fixture that an earlier run dragged into a folder
    # counted as present while not being on the screen this test looks at —
    # which showed up as "no csv on screen" and looked like a broken preview.
    req = urllib.request.Request(BASE + "/api/documents?folder=0",
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        have = {x["title"] for x in json.load(r).get("items", [])}

    for title, name, body in want:
        if title in have:
            continue
        b = "----sn" + uuid.uuid4().hex
        out = bytearray()
        for k, v in (("title", title), ("category", "other")):
            out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                    % (b, k, v)).encode()
        out += ('--%s\r\nContent-Disposition: form-data; name="file";'
                ' filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n'
                % (b, name)).encode()
        out += body + ("\r\n--%s--\r\n" % b).encode()
        rq = urllib.request.Request(
            BASE + "/api/documents", data=bytes(out), method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=" + b,
                     "Authorization": "Bearer " + token})
        with urllib.request.urlopen(rq, timeout=120) as r:
            r.read()


async def main():
    token = login_token()
    seed(token)
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

            # Reach Documents however this build routes it.
            got = await c.eval(CLICK % (
                "'button, a, [role=button]'",
                "/document/i.test((el.textContent||'').trim()) && el.offsetParent"),
                wait=False)
            await asyncio.sleep(3)

            here = await c.eval(
                "document.querySelector('.doc-grid, .folder-grid') ? 'yes' : 'no'",
                wait=False)
            check(here == "yes", "the Documents screen renders", (got, here))

            n = await c.eval("document.querySelectorAll('.doc-card').length", wait=False)
            # At least four, because the checks below index into the second
            # card and expect the type filter to leave some out.
            check(n >= 4, "the fixtures are on screen", n)

            # ---- selection -------------------------------------------------
            await c.eval("document.querySelector('.doc-pick').click()", wait=False)
            await asyncio.sleep(0.6)
            bar = await c.eval(
                "(() => {const b=document.querySelector('.selbar');"
                "return b ? b.querySelector('.selbar-n').textContent : 'none'})()",
                wait=False)
            check(bar == "1 selected", "picking a card raises the selection bar", bar)

            picked = await c.eval(
                "document.querySelectorAll('.doc-card.picked').length", wait=False)
            check(picked == 1, "and the card itself shows as picked", picked)

            # A second tap on a card should SELECT, not open the viewer — the
            # whole point of the selecting mode.
            await c.eval(
                "document.querySelectorAll('.doc-card')[1]"
                ".querySelector('.doc-hit').click()", wait=False)
            await asyncio.sleep(0.6)
            bar = await c.eval(
                "(() => {const b=document.querySelector('.selbar');"
                "return b ? b.querySelector('.selbar-n').textContent : 'none'})()",
                wait=False)
            check(bar == "2 selected",
                  "tapping a card while selecting adds it, not opens it", bar)
            viewer = await c.eval(
                "document.querySelector('.viewer, .doc-viewer') ? 'open' : 'closed'",
                wait=False)
            check(viewer == "closed", "and the viewer did not open", viewer)

            # ---- bulk star -------------------------------------------------
            await c.eval(
                "[...document.querySelectorAll('.selbar-act')]"
                ".find(b => b.title === 'Star').click()", wait=False)
            await asyncio.sleep(2)
            stars = await c.eval(
                "document.querySelectorAll('.doc-star').length", wait=False)
            check(stars >= 2, "the bulk star reaches the list", stars)
            gone = await c.eval(
                "document.querySelector('.selbar') ? 'still there' : 'cleared'",
                wait=False)
            check(gone == "cleared", "and the selection clears afterwards", gone)

            # ---- filters ---------------------------------------------------
            await c.eval(
                "[...document.querySelectorAll('.chip')]"
                ".find(b => b.textContent.includes('Filters')).click()", wait=False)
            await asyncio.sleep(0.6)
            panel = await c.eval(
                "document.querySelector('.doc-filters') ? 'open' : 'missing'", wait=False)
            check(panel == "open", "the filter panel opens", panel)

            types = await c.eval(
                "document.querySelectorAll('.doc-types .chip').length", wait=False)
            check(types == 7, "all seven type chips are there", types)

            dates = await c.eval(
                "document.querySelectorAll('.doc-dates input[type=date]').length",
                wait=False)
            check(dates == 2, "and both date inputs", dates)

            before = await c.eval("document.querySelectorAll('.doc-card').length",
                                  wait=False)
            await c.eval(
                "[...document.querySelectorAll('.doc-types .chip')]"
                ".find(b => b.textContent.includes('PDF')).click()", wait=False)
            await asyncio.sleep(2)
            after = await c.eval("document.querySelectorAll('.doc-card').length",
                                 wait=False)
            check(after < before and after > 0,
                  "choosing PDFs narrows the list", (before, after))

            await c.eval(
                "[...document.querySelectorAll('.chip')]"
                ".find(b => b.textContent.trim() === 'Clear').click()", wait=False)
            await asyncio.sleep(2)
            restored = await c.eval("document.querySelectorAll('.doc-card').length",
                                    wait=False)
            check(restored == before, "and Clear puts them all back",
                  (before, restored))

            # Preview runs before the folder work: the drag below moves a
            # document into a folder, and if that document is the CSV the
            # preview check fails with "no csv on screen" — which reads as
            # a broken preview rather than as a file that moved.
            # ---- text / CSV preview ----------------------------------------
            # Opens a .csv and expects a TABLE where a download card used to
            # be. Nothing in a typecheck can tell those apart.
            opened = await c.eval("""
            (async () => {
              const card = [...document.querySelectorAll('.doc-card')]
                .find(el => (el.textContent || '').includes(TITLE));
              if (!card) return 'no csv on screen';
              card.querySelector('.doc-hit').click();
              await new Promise(r => setTimeout(r, 2500));
              const t = document.querySelector('.csvprev');
              if (!t) return 'no table: ' +
                (document.querySelector('.viewer-pdf') ? 'download card' : 'nothing');
              const first = [...t.querySelectorAll('tr')[0].cells].map(c => c.textContent);
              const rows = t.querySelectorAll('tr').length;
              return JSON.stringify({first, rows});
            })()
            """.replace("TITLE", json.dumps(CSV_TITLE)))
            ok = str(opened).startswith("{")
            check(ok, "a CSV opens as a table, not a download card", opened)
            if ok:
                got = json.loads(opened)
                check(got["first"] == ["Item", "Amount", "Note"],
                      "the header row is what the file says", got["first"])
                # The row with a comma inside a quoted field must be ONE row of
                # three cells, not a torn one — proved end to end, not just at
                # the endpoint.
                cells = await c.eval(
                    "(() => {const r=[...document.querySelectorAll('.csvprev tr')]"
                    " .find(x => /Sharma/.test(x.textContent));"
                    " return r ? [...r.cells].map(c=>c.textContent) : null})()",
                    wait=False)
                check(cells == ["Sharma, Priya", "2500", "quoted, comma"],
                      "a quoted comma stays inside one cell", cells)

            await c.eval(
                "[...document.querySelectorAll('.viewer-btn')]"
                ".find(b => b.getAttribute('aria-label') === 'Close')?.click()",
                wait=False)
            await asyncio.sleep(1)

            # ---- folders: rename, and drag a document onto one -------------
            await c.eval(
                "(() => {const b=[...document.querySelectorAll('.crumb-new')][0];"
                " if (b) b.click(); return !!b})()", wait=False)
            await asyncio.sleep(0.6)
            sheet = await c.eval(
                "document.querySelector('.sheet input') ? 'open' : 'missing'", wait=False)
            check(sheet == "open", "the new-folder sheet opens", sheet)
            if sheet == "open":
                await c.eval(
                    "(() => {const i=document.querySelector('.sheet input');"
                    " const set=Object.getOwnPropertyDescriptor("
                    "   window.HTMLInputElement.prototype,'value').set;"
                    " set.call(i, %s);" % json.dumps(FOLDER) +
                    " i.dispatchEvent(new Event('input',{bubbles:true}));"
                    " return i.value})()", wait=False)
                await asyncio.sleep(0.4)
                await c.eval(
                    "[...document.querySelectorAll('.sheet button')]"
                    ".find(b => /create/i.test(b.textContent)).click()", wait=False)
                await asyncio.sleep(2)

            tiles = await c.eval(
                "document.querySelectorAll('.folder-tile').length", wait=False)
            check(tiles > 0, "the folder appears", tiles)

            ren = await c.eval(
                "document.querySelectorAll('.folder-ren').length", wait=False)
            check(ren == tiles, "every folder offers rename", (ren, tiles))

            await c.eval(
                "(() => {const t=[...document.querySelectorAll('.folder-tile')]"
                " .find(x => x.querySelector('.folder-name')"
                "   ?.textContent.trim() === %s);"
                " if (!t) return 'no such folder';"
                " t.querySelector('.folder-ren').click(); return 'clicked'})()"
                % json.dumps(FOLDER), wait=False)
            await asyncio.sleep(0.6)
            # Rename must OPEN WITH THE CURRENT NAME. An empty box is a rename
            # that quietly becomes "type the whole thing again".
            pre = await c.eval(
                "(() => {const i=document.querySelector('.sheet input');"
                " return i ? i.value : 'no sheet'})()", wait=False)
            check(pre == FOLDER, "rename opens with the current name", pre)
            btn = await c.eval(
                "(() => {const b=[...document.querySelectorAll('.sheet button')]"
                "  .find(x => /rename/i.test(x.textContent));"
                " return b ? b.textContent.trim() : 'missing'})()", wait=False)
            check(btn == "Rename", "and its button says Rename, not Create", btn)
            await c.eval(
                "[...document.querySelectorAll('.sheet .sheet-x, .sheet button')]"
                ".find(b => /close|✕|×/i.test(b.textContent||b.getAttribute('aria-label')||''))"
                "?.click()", wait=False)
            await asyncio.sleep(0.6)

            # Dragging a card onto a folder. Synthesised, because a headless
            # browser has no pointer — what this proves is that the handlers are
            # bound and that the drop reaches the move, not that the gesture
            # feels right.
            moved = await c.eval("""
            (async () => {
              const card = document.querySelector('.doc-card');
              const tile = [...document.querySelectorAll('.folder-tile')]
                .find(x => x.querySelector('.folder-name')
                  ?.textContent.trim() === __FOLDER__);
              if (!card || !tile) return 'nothing to drag';
              const dt = new DataTransfer();
              card.dispatchEvent(new DragEvent('dragstart',
                {bubbles: true, dataTransfer: dt}));
              if (!dt.getData('text/document-id')) return 'dragstart set nothing';
              tile.dispatchEvent(new DragEvent('dragover',
                {bubbles: true, dataTransfer: dt}));
              await new Promise(r => setTimeout(r, 200));
              const lit = tile.className.includes('drop-on');
              tile.dispatchEvent(new DragEvent('drop',
                {bubbles: true, dataTransfer: dt}));
              await new Promise(r => setTimeout(r, 1500));
              return (lit ? 'lit' : 'not lit') + ':' +
                     document.querySelectorAll('.doc-card').length;
            })()
            """.replace("__FOLDER__", json.dumps(FOLDER)))
            check(str(moved).startswith("lit:"),
                  "the folder lights up while a document is over it", moved)
            # One document left the listing, because it is now inside the folder.
            check(str(moved).endswith(":" + str(before - 1)),
                  "and dropping it moves it in", (moved, before))

            # ---- console ----------------------------------------------------
            errs = [e for e in c.events
                    if e.get("method") == "Log.entryAdded"
                    and e["params"]["entry"].get("level") == "error"]
            # Two kinds of noise that are not this screen:
            #  * a 404 for the thumbnail of a document that has none;
            #  * /api/update 403, which is every non-admin account. The app has
            #    polled it since August and catches it deliberately ("never a
            #    red screen") — the console line is the browser's network log,
            #    not an unhandled error.
            def noise(e):
                t = e["params"]["entry"].get("text", "")
                u = e["params"]["entry"].get("url") or ""
                return "404" in t or "/api/update" in u
            real = [e for e in errs if not noise(e)]
            check(not real, "no console errors",
                  [(e["params"]["entry"].get("url") or "?")
                   + " :: " + e["params"]["entry"]["text"][:70] for e in real][:4])
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
