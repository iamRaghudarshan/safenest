"""Exercise every user-facing module against the THROWAWAY server on 8090.

Not a unit test and not a smoke ping: each module is created, read back,
edited, and deleted, because "the endpoint returned 200" has hidden real bugs
in this codebase before — a wrong field name is accepted, defaulted, and
silently dropped (that is exactly how the screenshots came to read "invested
Rs 0" and "Other").

So every check reads the value BACK and compares it. Port 8090 only: the live
server on 8080 holds real records.
"""
import io
import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

B = "http://127.0.0.1:8090"
EMAIL = "sweep@example.com"
PW = "SweepHouse#2026"
TOKEN = None
PASS = FAIL = 0
today = date.today()


def same(a, b):
    """9500 and 9500.0 are the same number; their string forms are not."""
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


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
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:160]}


def upload(path, filename, blob, extra=None):
    boundary = "----sw" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts = []
    for k, v in (extra or {}).items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    body = b"".join(parts) + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(B + path, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                          "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw.startswith(b"{") else {})
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:160]}


def rows(payload):
    """Every list endpoint here answers with one of these shapes."""
    if isinstance(payload, list):
        return payload
    for k in ("items", "rows", "photos", "documents", "results", "data"):
        v = payload.get(k)
        if isinstance(v, list):
            return v
    return []


def png(colour=(70, 120, 200)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), colour).save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ account
# Registration is closed once an instance has a user, so the account is made
# directly in the throwaway database by create_admin.py before this runs.
st, r = call("/api/auth/login", {"email": EMAIL, "password": PW}, tok=False)
assert st == 200, r
TOKEN = r["token"]
print(f"ACCOUNT\n  signed in as {EMAIL}\n")

st, me = call("/api/auth/me")
check("session reports the right account", me.get("user", me).get("email") == EMAIL)

# ------------------------------------------------------- record modules CRUD
print("\nRECORD MODULES — create, read back, edit, delete")

CASES = [
    ("expenses", "/api/expenses",
     {"kind": "expense", "category": "Groceries", "amount": 1234, "method": "UPI",
      "txn_date": today.isoformat(), "note": "sweep groceries"},
     ("category", "Groceries"), {"category": "Fuel"}, ("category", "Fuel")),
    ("income", "/api/expenses",
     {"kind": "income", "category": "Salary", "amount": 50000, "method": "Bank transfer",
      "txn_date": today.isoformat(), "note": "sweep salary"},
     ("kind", "income"), {"amount": 60000}, ("amount", 60000)),
    ("cards", "/api/cards",
     {"bank": "Sweep Bank", "last4": "1234", "credit_limit": 100000,
      "statement_amount": 2500, "due_date": (today + timedelta(days=10)).isoformat()},
     ("bank", "Sweep Bank"), {"credit_limit": 150000}, ("credit_limit", 150000)),
    ("loans", "/api/loans",
     {"lender": "Sweep Bank", "loan_type": "Home", "principal": 500000,
      "outstanding": 400000, "emi": 9000, "interest_rate": 8.5},
     ("lender", "Sweep Bank"), {"emi": 9500}, ("emi", 9500)),
    ("insurance", "/api/insurance",
     {"provider": "Sweep Insure", "policy_no": "SW-1", "policy_type": "Health",
      "premium": 12000, "sum_assured": 500000, "frequency": "yearly",
      "renewal_date": (today + timedelta(days=60)).isoformat()},
     ("policy_type", "Health"), {"premium": 13000}, ("premium", 13000)),
    ("investments", "/api/investments",
     {"name": "Sweep Fund", "invest_type": "Mutual fund", "broker": "Sweep Broker",
      "invested_amount": 100000, "current_value": 125000},
     ("invest_type", "Mutual fund"), {"current_value": 130000}, ("current_value", 130000)),
    ("reminders", "/api/reminders",
     {"title": "Sweep reminder", "due_date": (today + timedelta(days=5)).isoformat()},
     ("title", "Sweep reminder"), {"title": "Sweep renamed"}, ("title", "Sweep renamed")),
    ("todos", "/api/todos", {"title": "Sweep todo"},
     ("title", "Sweep todo"), {"title": "Sweep todo 2"}, ("title", "Sweep todo 2")),
    ("notes", "/api/notes", {"title": "Sweep note", "body": "a note"},
     ("title", "Sweep note"), {"title": "Sweep note 2"}, ("title", "Sweep note 2")),
    ("habits", "/api/habits", {"name": "Sweep habit"},
     ("name", "Sweep habit"), {"name": "Sweep habit 2"}, ("name", "Sweep habit 2")),
]

for label, path, payload, (read_k, read_v), edit, (ek, ev) in CASES:
    st, created = call(path, payload)
    if st not in (200, 201):
        check(f"{label}: create", False, f"{st} {created}")
        continue
    obj = created.get("item", created)
    rid = obj.get("id") or created.get("id")
    check(f"{label}: create", bool(rid), str(created)[:90])
    if not rid:
        continue

    st, listed = call(path)
    found = next((x for x in rows(listed) if x.get("id") == rid), None)
    check(f"{label}: reads back with the value it was given",
          found is not None and same(found.get(read_k), read_v),
          f"wanted {read_k}={read_v}, got {found.get(read_k) if found else 'missing'}")

    st, _ = call(f"{path}/{rid}", edit, method="PUT")
    st2, listed2 = call(path)
    after = next((x for x in rows(listed2) if x.get("id") == rid), None)
    check(f"{label}: edit sticks",
          after is not None and same(after.get(ek), ev),
          f"wanted {ek}={ev}, got {after.get(ek) if after else 'missing'}")

    st, _ = call(f"{path}/{rid}", method="DELETE")
    st2, listed3 = call(path)
    check(f"{label}: delete removes it",
          all(x.get("id") != rid for x in rows(listed3)), f"delete said {st}")

# ------------------------------------------------------------------- vault
print("\nVAULT — the encrypted one")
st, v = call("/api/vault", {"title": "Sweep login", "username": "sweep",
                            "category": "Utilities", "password": "S3cr3t!sweep"})
vid = v.get("id")
check("vault: create", st in (200, 201) and bool(vid), str(v)[:80])
st, listed = call("/api/vault")
row = next((x for x in rows(listed) if x.get("id") == vid), None)
check("vault: listed without leaking the password",
      row is not None and "password" not in row and row.get("has_password") is True,
      str(row)[:100])
st, rev = call(f"/api/vault/{vid}/reveal", {})
check("vault: reveal returns the real secret", rev.get("password") == "S3cr3t!sweep",
      str(rev)[:80])
call(f"/api/vault/{vid}", method="DELETE")

# ------------------------------------------------------------------ media
print("\nMEDIA — gallery and documents")
st, g = upload("/api/gallery/upload", "sweep.png", png())
check("gallery: upload accepted", st in (200, 201), str(g)[:90])
st, gl = call("/api/gallery")
check("gallery: the photo is listed", len(rows(gl)) >= 1, f"{len(rows(gl))} photos")

st, d = upload("/api/documents", "sweep-doc.png", png((200, 170, 90)),
               extra={"title": "Sweep document", "category": "Other"})
check("documents: upload accepted", st in (200, 201), str(d)[:90])
st, dl = call("/api/documents")
doc = next((x for x in rows(dl) if x.get("title") == "Sweep document"), None)
check("documents: stored with its title and category",
      doc is not None and same(doc.get("category"), "Other"), str(doc)[:90])

# ------------------------------------------------------------------- reads
print("\nREAD SURFACES")
for label, path in [("dashboard", "/api/dashboard"), ("search", "/api/search?q=sweep"),
                    ("activity", "/api/activity"), ("briefing", "/api/briefing"),
                    ("notification settings", "/api/notifications/settings"),
                    ("storage diagnose", "/api/storage/diagnose"),
                    ("household", "/api/household"), ("masters types", "/api/masters/types"),
                    ("people", "/api/people"), ("devices", "/api/devices")]:
    st, r = call(path)
    check(f"{label} answers", st == 200, f"http {st} {str(r)[:70]}")

# --------------------------------------------------------------- protection
print("\nAUTH BOUNDARY")
st, _ = call("/api/expenses", tok=False)
check("an unauthenticated read is refused", st in (401, 403), f"http {st}")
st, _ = call("/api/vault", tok=False)
check("the vault is refused too", st in (401, 403), f"http {st}")

print(f"\n{PASS} passed, {FAIL} failed")
