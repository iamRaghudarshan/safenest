"""Phase 4: regression. Exercise it, do not eyeball it.

The licence form is submitted for real against the live endpoint, because the
whole point of this pass was not to break it — and a form that "looks fine" is
the single easiest thing to break with a markup change.
"""
import asyncio
import json
import subprocess
import time
import urllib.request

import sys

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9420
URL = sys.argv[1] if len(sys.argv) > 1 else "https://safenesthub.in/"
PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def tgt():
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as r:
                for x in json.load(r):
                    if x.get("type") == "page":
                        return x["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("no target")


async def main():
    p = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         "--user-data-dir=C:/Users/Pro-TEAM/AppData/Local/Temp/claude/cdp-reg",
         "--no-first-run", "--disable-gpu", "--window-size=1440,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with websockets.connect(tgt(), max_size=200_000_000) as ws:
            n = 0
            events = []

            async def send(m, **kw):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": m, "params": kw}))
                while True:
                    msg = json.loads(await ws.recv())
                    if "id" not in msg:
                        events.append(msg)
                        continue
                    if msg["id"] == n:
                        return msg.get("result", {})

            async def ev(e):
                r = await send("Runtime.evaluate", expression=e, returnByValue=True,
                               awaitPromise=True)
                if r.get("exceptionDetails"):
                    return "ERR " + json.dumps(r["exceptionDetails"])[:160]
                return r.get("result", {}).get("value")

            await send("Page.enable")
            await send("Runtime.enable")
            await send("Log.enable")
            await send("Page.navigate", url=URL)
            await asyncio.sleep(9)

            print("STRUCTURE")
            check("one h1", await ev("document.querySelectorAll('h1').length") == 1)
            check("<main> present", await ev("!!document.querySelector('main#main')"))
            check("skip link targets main",
                  await ev("document.querySelector('.skip-link').getAttribute('href')") == "#main")
            check("nav landmark labelled",
                  bool(await ev("document.querySelector('body > nav').getAttribute('aria-label')")))
            check("all sections present",
                  await ev("document.querySelectorAll('main section').length") >= 8,
                  await ev("document.querySelectorAll('main section').length"))

            print("\nCONTENT")
            check("12 module cards",
                  await ev("document.querySelectorAll('#features-grid .feat').length") == 12)
            check("8 capability cards",
                  await ev("document.querySelectorAll('#extras-grid .feat').length") == 8)
            # The screenshot strip was removed deliberately: the site shows
            # illustrations now, not pictures of the application.
            check("no screenshot tabs (removed on purpose)",
                  await ev("document.querySelectorAll('.shot-tab').length") == 0)
            check("3 section illustrations",
                  await ev("document.querySelectorAll('img.art').length") == 3)
            check("6 FAQ items",
                  await ev("document.querySelectorAll('.faq-item').length") == 6)
            check("4 footer columns",
                  await ev("document.querySelectorAll('.foot-col').length") == 4)
            check("AI BIT gate trigger intact",
                  await ev("document.querySelectorAll('.dl-trigger').length") >= 1)

            print("\nSEO")
            for tag, sel in [("canonical", "link[rel=canonical]"),
                             ("og:title", "meta[property='og:title']"),
                             ("og:image", "meta[property='og:image']"),
                             ("twitter:card", "meta[name='twitter:card']"),
                             ("JSON-LD", "script[type='application/ld+json']")]:
                check(tag, await ev(f"!!document.querySelector(\"{sel}\")"))

            print("\nIMAGES")
            broken = await ev("[...document.querySelectorAll('img')]"
                              ".filter(i=>i.complete&&i.naturalWidth===0)"
                              ".map(i=>i.getAttribute('src')).join(', ')")
            check("no broken images", not broken, broken)

            print(chr(10) + "ILLUSTRATIONS")
            # The screenshot strip was removed on purpose -- the site shows
            # drawings now, not pictures of the application. The old tab loop
            # kept "passing" with zero tabs, which is a test that cannot fail.
            # These carry loading="lazy" and the lowest one sits ~7000px down,
            # so checking straight after load asks the browser for something it
            # is entitled not to have fetched. A visitor scrolls; so does this.
            await ev("(async()=>{for(let y=0;y<document.body.scrollHeight;y+=700)"
                     "{window.scrollTo(0,y);await new Promise(r=>setTimeout(r,90));}"
                     "window.scrollTo(0,0);})()")
            await asyncio.sleep(2.0)
            bad = await ev(
                "[...document.querySelectorAll('img.art, .hero-art img')]"
                ".filter(function(i){return !i.complete || i.naturalWidth===0;})"
                ".map(function(i){return i.getAttribute('src');}).join(', ')")
            check("every illustration renders", not bad, bad)
            check("all illustrations are vector",
                  await ev("[...document.querySelectorAll('img.art, .hero-art img')]"
                           ".every(function(i){return (i.getAttribute('src')||'').indexOf('.svg')>-1;})"))
            leftover = await ev(
                "[...document.images].filter(function(i){"
                "var s=i.getAttribute('src')||'';"
                "return /[.](webp|png|jpg|jpeg)/.test(s) && !/logo|icon/.test(s);})"
                ".map(function(i){return i.getAttribute('src');}).join(', ')")
            check("no app screenshots remain anywhere", not leftover, leftover)

            print("\nFORMS — submitted for real")
            r = await ev("""(async () => {
              document.getElementById('rn').value = 'Regression Check';
              document.getElementById('re').value = 'regression+web@example.com';
              document.getElementById('rm').value = 'Automated post-redesign check — please ignore.';
              document.getElementById('req').dispatchEvent(
                new Event('submit', {bubbles:true, cancelable:true}));
              await new Promise(r => setTimeout(r, 4000));
              return JSON.stringify({
                formHidden: document.getElementById('req').style.display === 'none',
                okShown: getComputedStyle(document.getElementById('req-ok')).display !== 'none',
                err: document.getElementById('req-err').textContent,
                dlShown: getComputedStyle(document.getElementById('req-download')).display !== 'none'
              });
            })()""")
            d = json.loads(r) if r and r.startswith("{") else {}
            check("licence form submits", d.get("formHidden") and d.get("okShown"),
                  f"err={d.get('err')!r}")
            check("download unlocks after request", d.get("dlShown"))
            check("error region is a live region",
                  await ev("document.getElementById('req-err').getAttribute('role')") == "alert")
            check("support form still wired", await ev("!!document.getElementById('sup')"))

            print("\nRESPONSIVE")
            for w in (320, 375, 430, 768, 1024, 1440, 1920):
                await send("Emulation.setDeviceMetricsOverride", width=w, height=880,
                           deviceScaleFactor=1, mobile=w < 768)
                await asyncio.sleep(0.9)
                over = await ev("document.documentElement.scrollWidth") - w
                check(f"no horizontal overflow at {w}px", over <= 2, f"+{over}px")
            await send("Emulation.clearDeviceMetricsOverride")

            errs = [e for e in events
                    if e.get("method") in ("Log.entryAdded", "Runtime.exceptionThrown")
                    and "cloudflareinsights" not in json.dumps(e)
                    and "Password field" not in json.dumps(e)]
            print()
            check("no console errors", len(errs) == 0,
                  json.dumps(errs[0].get("params", {}))[:200] if errs else "")

            print(f"\n{PASS} passed, {FAIL} failed")
    finally:
        p.terminate()


asyncio.run(main())
