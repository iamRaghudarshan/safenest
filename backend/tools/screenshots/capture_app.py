"""Photograph the real app, from the throwaway instance, for the storefront.

These replace a CSS drawing of a dashboard with the dashboard. The difference
matters for this product specifically: the whole pitch is "it runs on your own
machine", and a hand-drawn approximation of the UI is the one image a sceptical
visitor cannot check.
"""
import asyncio
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9240
BASE = "http://127.0.0.1:8099"
OUT = Path(__file__).resolve().parent / "shots"
PROFILE = r"C:\Users\Pro-TEAM\AppData\Local\Temp\claude\cdp-app"

# label -> (route hash, width, height)
SHOTS = [
    ("dashboard", "home", 1280, 860),
    ("gallery", "gallery", 1280, 860),
    ("documents", "documents", 1280, 860),
    ("expenses", "expenses", 1280, 860),
    ("vault", "vault", 1280, 860),
    ("investments", "investments", 1280, 860),
    ("insurance", "insurance", 1280, 860),
    ("phone-home", "home", 400, 820),
]


def token():
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"email": "priya@example.com", "password": "DemoHouse#2026"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["token"]


def tgt():
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as r:
                for t in json.load(r):
                    if t.get("type") == "page":
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("no target")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tok = token()
    p = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE}", "--no-first-run", "--disable-gpu",
         "--hide-scrollbars", "--force-device-scale-factor=2",
         "--window-size=1280,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with websockets.connect(tgt(), max_size=200_000_000) as ws:
            n = 0

            async def send(m, **kw):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": m, "params": kw}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        return msg.get("result", {})

            async def ev(expr):
                r = await send("Runtime.evaluate", expression=expr, returnByValue=True)
                return r.get("result", {}).get("value")

            await send("Page.enable")
            await send("Runtime.enable")
            await send("Page.navigate", url=BASE + "/")
            await asyncio.sleep(4)
            await ev(f"localStorage.setItem('finmate.token', {json.dumps(tok)})")

            for label, route, w, h in SHOTS:
                # Device metrics per shot so the phone frames get a real phone
                # viewport, not a desktop layout squeezed into a narrow window.
                await send("Emulation.setDeviceMetricsOverride", width=w, height=h,
                           deviceScaleFactor=2, mobile=w < 600)
                await send("Page.navigate", url=BASE + "/")
                await asyncio.sleep(5)
                if route:
                    # The app does not route on the URL — the first attempt set
                    # location.hash and got seven identical screenshots of Home.
                    # Navigation is a click, so click it.
                    clicked = await ev(
                        "(() => {const want = " + json.dumps(route.lower()) + ";"
                        "const els=[...document.querySelectorAll('button,a,[role=button],li')];"
                        "const hit=els.find(e=>(e.textContent||'').trim().toLowerCase()===want);"
                        "if(hit){hit.click();return 'clicked'} return 'NOT FOUND'})()")
                    print(f"     nav {route}: {clicked}")
                    await asyncio.sleep(4)
                r = await send("Page.captureScreenshot", format="png")
                f = OUT / f"{label}.png"
                f.write_bytes(base64.b64decode(r["data"]))
                print(f"  {label:16} {w}x{h}  {f.stat().st_size // 1024} KB")
    finally:
        p.terminate()


asyncio.run(main())
