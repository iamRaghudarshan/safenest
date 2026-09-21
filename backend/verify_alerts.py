"""Reminders, alerts and mail — proven end to end on a THROWAWAY instance.

Mail is proven against a real SMTP conversation with a catcher on 127.0.0.1,
not by asserting a 200: "it returned OK" is exactly what this codebase does
when mail is NOT configured, because every caller guards with
mailer.is_configured() and skips silently by design.

Port 8091 only. Production on 8080 is never written to.
"""
import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

B = "http://127.0.0.1:8091"
EMAIL, PW = "alerts@example.com", "AlertHouse#2026"
TOKEN = None
PASS = FAIL = 0
CAUGHT = []          # messages the fake SMTP server received


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   {detail}")


def call(path, body=None, method=None, tok=True):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok and TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(B + path, data=data,
                                 method=method or ("POST" if data is not None else "GET"),
                                 headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:200]}


def rows(p):
    if isinstance(p, list):
        return p
    for k in ("items", "rows", "notifications", "results", "data"):
        if isinstance(p.get(k), list):
            return p[k]
    return []


# ------------------------------------------------------------ fake SMTP
class Catcher(asyncio.Protocol):
    """Barely an SMTP server: enough of the conversation for smtplib to finish."""

    def connection_made(self, transport):
        self.t = transport
        self.buf = b""
        self.in_data = False
        self.t.write(b"220 localhost SafeNest test catcher\r\n")

    def data_received(self, data):
        if self.in_data:
            self.buf += data
            if b"\r\n.\r\n" in self.buf:
                CAUGHT.append(self.buf.decode("utf-8", "replace"))
                self.in_data = False
                self.buf = b""
                self.t.write(b"250 OK queued\r\n")
            return
        for line in data.split(b"\r\n"):
            if not line:
                continue
            up = line.upper()
            if up.startswith((b"EHLO", b"HELO")):
                self.t.write(b"250-localhost\r\n250 OK\r\n")
            elif up.startswith((b"MAIL", b"RCPT")):
                self.t.write(b"250 OK\r\n")
            elif up.startswith(b"DATA"):
                self.in_data = True
                self.t.write(b"354 End with .\r\n")
            elif up.startswith(b"QUIT"):
                self.t.write(b"221 Bye\r\n")
                self.t.close()
            else:
                self.t.write(b"250 OK\r\n")


def start_smtp(port=1025):
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        srv = loop.run_until_complete(loop.create_server(Catcher, "127.0.0.1", port))
        loop.run_forever()
    threading.Thread(target=run, daemon=True).start()
    time.sleep(1.5)


start_smtp()
print("SMTP catcher listening on 127.0.0.1:1025\n")

st, r = call("/api/auth/login", {"email": EMAIL, "password": PW}, tok=False)
assert st == 200, r
TOKEN = r["token"]

# =================================================================== MAIL
print("MAIL")
st, _ = call("/api/admin/mail", {
    "enabled": True, "host": "127.0.0.1", "port": 1025, "security": "none",
    "username": "", "password": "", "from_addr": "safenest@example.com",
    "from_name": "SafeNest"}, method="PUT")
check("SMTP settings saved", st == 200, f"http {st}")

st, m = call("/api/admin/mail")
check("it reports itself configured", bool(m.get("host")) and m.get("enabled") in (True, 1),
      str(m)[:110])

before = len(CAUGHT)
st, t = call("/api/admin/mail/test", {"to": "owner@example.com"})
for _ in range(20):
    if len(CAUGHT) > before:
        break
    time.sleep(0.5)
check("a test email is actually delivered over SMTP", len(CAUGHT) > before,
      f"http {st} {str(t)[:90]}; caught {len(CAUGHT)}")
if CAUGHT:
    body = CAUGHT[-1]
    check("the message carries a subject and the right sender",
          "Subject:" in body and "safenest@example.com" in body, body[:120])

st, log = call("/api/admin/mail/log")
check("the mail queue is readable (a direct test send is not queued, by design)",
      st == 200 and "items" in log, str(log)[:110])

# ============================================================== REMINDERS
print("\nREMINDERS — the scheduler rings them")
now = datetime.now()
soon = (now + timedelta(minutes=1)).strftime("%H:%M")
st, rem = call("/api/reminders", {"title": "Ring me", "due_date": date.today().isoformat(),
                                  "due_time": soon, "notes": "scheduler test"})
rid = (rem.get("item", rem)).get("id")
check("a reminder with an hour on it is accepted", bool(rid), str(rem)[:100])

st, listed = call("/api/reminders")
check("it is listed before it fires", any(x.get("id") == rid for x in rows(listed)))

print(f"  waiting for the 60s scheduler tick (due {soon})...")
fired = False
for _ in range(14):
    time.sleep(10)
    st, inbox = call("/api/notifications/inbox")
    if any("Ring me" in str(n.get("title", "")) + str(n.get("body", "")) for n in rows(inbox)):
        fired = True
        break
check("THE ONE THAT MATTERS: the reminder actually rang", fired,
      "no notification arrived within ~2 minutes")

# ================================================= ALERTS FROM OTHER DATES
print("\nALERTS — dates on records raise their own warnings")
call("/api/insurance", {"provider": "Alert Insure", "policy_no": "AL-1",
                        "policy_type": "Health", "premium": 1000, "sum_assured": 100000,
                        "frequency": "yearly",
                        "renewal_date": (date.today() + timedelta(days=3)).isoformat()})
call("/api/cards", {"bank": "Alert Bank", "last4": "4444", "credit_limit": 50000,
                    "due_date": (date.today() + timedelta(days=2)).isoformat()})
call("/api/loans", {"lender": "Alert Bank", "loan_type": "Car", "principal": 100000,
                    "outstanding": 80000, "emi": 5000,
                    "next_due_date": (date.today() + timedelta(days=4)).isoformat()})

st, dash = call("/api/dashboard")
blob = json.dumps(dash).lower()
dues = (dash.get("stats") or {}).get("duesCount", 0)
check("all three dated records raise a due alert (insurance + card + loan)",
      dues >= 3, f"duescount={dues}")
check("the due items are named somewhere on the dashboard", "alert bank" in blob, blob[:120])

st, brief = call("/api/briefing")
check("the briefing answers", st == 200, f"http {st}")

# ======================================================== DIGEST / OFFLINE
print("\nDIGEST")
st, _ = call("/api/notifications/settings",
             {"enabled": True, "sendHour": 9, "sendMinute": 0,
              "includeBills": True, "includeReminders": True, "includeExpiry": True},
             method="PUT")
st, s = call("/api/notifications/settings")
check("digest can be switched on and keeps its schedule",
      s.get("enabled") is True and s.get("sendHour") == 9, str(s)[:110])

before = len(CAUGHT)
st, d = call("/api/notifications/test", {})
time.sleep(2)
check("a push digest with no device registered is refused, clearly",
      st == 422 and "device" in str(d).lower(), f"http {st} {str(d)[:90]}")

st, inbox = call("/api/notifications/inbox")
check("the in-app inbox is populated (works with no internet at all)",
      len(rows(inbox)) >= 1, f"{len(rows(inbox))} items")
st, unread = call("/api/notifications/unread")
check("unread count is reported", st == 200, f"http {st}")

print(f"\n{PASS} passed, {FAIL} failed")
print(f"emails actually delivered over SMTP during this run: {len(CAUGHT)}")
