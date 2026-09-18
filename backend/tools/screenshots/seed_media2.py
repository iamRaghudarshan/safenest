"""Fill the throwaway instance's gallery and documents with real-looking paperwork.

The old set put soft landscape gradients in the gallery -- a sky, a sun, two
hills -- which at thumbnail size is a coloured rectangle rather than a
photograph, and which also contradicted the copy beside it on the website. The
documents were pages of grey BARS standing in for text, so the documents screen
photographed as a loading skeleton.

Everything here is a thing a household actually keeps, rendered with real type:
receipts and bills photographed on a table, warranty cards, policy schedules and
statements scanned flat. Every company, account number and person is invented.
"""
import mimetypes
import random
import urllib.error
import urllib.request
import uuid
import json

import paperwork as P

B = "http://127.0.0.1:8099"
EMAIL, PW = "priya@example.com", "DemoHouse#2026"
TOKEN = None
random.seed(23)


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:200]}


def upload(path, filename, blob, field="file", extra=None):
    boundary = "----sn" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts = []
    for k, v in (extra or {}).items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                 f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
    body = b"".join(parts) + blob + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        B + path, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, r.read()[:160]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:220]


st, r = call("/api/auth/login", {"email": EMAIL, "password": PW})
assert st == 200, r
TOKEN = r["token"]
print("signed in")

# ------------------------------------------------------------------ builders
def r_groceries():
    return P.receipt("GREEN BASKET", "Willow Road, Bengaluru", [
        ("Toor dal 1kg", "1 x 148.00", 148.0), ("Milk 500ml", "4 x 27.00", 108.0),
        ("Bananas", "1.2 kg x 58.00", 69.6), ("Atta 5kg", "1 x 285.00", 285.0),
        ("Curd 400g", "2 x 45.00", 90.0), ("Detergent", "1 x 219.00", 219.0),
    ], "UPI  ****4417", "17-09-2026  19:42")


def r_fuel():
    return P.receipt("VERTEX FUELS", "Outer Ring Road", [
        ("Petrol", "22.40 L x 93.75", 2100.0),
    ], "Card  ****4821", "16-09-2026  08:15")


def r_pharmacy():
    return P.receipt("CORNER CHEMIST", "Second Cross", [
        ("Paracetamol 650", "1 x 32.00", 32.0), ("Cough syrup", "1 x 148.00", 148.0),
        ("Vitamin D3", "1 x 265.00", 265.0), ("Bandage roll", "2 x 87.50", 175.0),
    ], "Cash", "09-09-2026  17:05")


def r_dining():
    return P.receipt("THE OLIVE ROOM", "Table 12  |  4 covers", [
        ("Soup of the day", "2 x 220.00", 440.0), ("Pasta arrabiata", "1 x 380.00", 380.0),
        ("Paneer tikka", "1 x 340.00", 340.0), ("Lime soda", "4 x 120.00", 480.0),
        ("Service charge", "", 220.0),
    ], "Card  ****9036", "14-09-2026  21:10")


def r_school():
    return P.receipt("MEADOW HIGH SCHOOL", "Fee receipt  |  Term 2", [
        ("Tuition fee", "", 15000.0), ("Activity fee", "", 2200.0),
        ("Library", "", 800.0), ("Transport", "", 500.0),
    ], "Bank transfer", "01-09-2026  10:30")


def b_power():
    return P.bill("NORTHLINE POWER", "Electricity supply", "AC-4471-2209", "Aug 2026",
                  "Rs 3,420.00", "28 Sep 2026",
                  [("Energy charges  292 units", "2,736.00"), ("Fixed charges", "180.00"),
                   ("Taxes and levies", "504.00")])


def b_water():
    return P.bill("CITYWATER", "Water and sewerage", "W-88-104417", "Jul-Sep 2026",
                  "Rs 1,180.00", "04 Oct 2026",
                  [("Consumption  38 kL", "760.00"), ("Sewerage", "260.00"),
                   ("Meter rent", "160.00")])


def b_fibre():
    return P.bill("NIMBUS FIBRE", "Broadband  |  300 Mbps", "NF-2209-8841", "Sep 2026",
                  "Rs 1,299.00", "22 Sep 2026",
                  [("Monthly plan", "1,100.00"), ("GST", "199.00")])


def b_service():
    return P.bill("EASTGATE MOTORS", "Vehicle service invoice", "JOB-44172", "Sep 2026",
                  "Rs 6,480.00", "on collection",
                  [("Engine oil + filter", "3,240.00"), ("Brake pads (front)", "2,100.00"),
                   ("Labour", "1,140.00")])


def w_fridge():
    return P.warranty("Solace 253L Refrigerator", "SL-RT28C3452", "0AFD93K2200119X",
                      "12 Feb 2026", "12 Feb 2028")


def w_tv():
    return P.warranty("Vertex 55\" LED Television", "VX-55U7300", "VX55K220911447",
                      "03 Nov 2025", "03 Nov 2027")


def w_washer():
    return P.warranty("Solace Front Load Washer", "SL-WM7145", "0BQ71M2209884",
                      "28 Jun 2026", "28 Jun 2029")


def p_health():
    return P.policy("SENTINEL HEALTH", "Family floater — health", "SH-88421907",
                    "Priya Sharma", "Rs 10,00,000", "Rs 28,400 / year", "25 Oct 2026")


def p_motor():
    return P.policy("HARBOUR GENERAL", "Private car — comprehensive", "HG-2209417",
                    "Priya Sharma", "Rs 9,00,000", "Rs 14,200 / year", "29 Sep 2026")


def p_life():
    return P.policy("LUMEN LIFE", "Term life cover", "LL-4471203",
                    "Priya Sharma", "Rs 1,00,00,000", "Rs 21,600 / year", "22 Dec 2026")


def s_salary():
    return P.statement("Salary slip — September 2026", "Havenpoint Design Studio", [
        ("Basic", "1,08,000.00"), ("House rent allowance", "43,200.00"),
        ("Special allowance", "38,400.00"), ("Provident fund", "-12,960.00"),
        ("Professional tax", "-200.00"), ("Income tax deducted", "-18,440.00"),
    ], "Net pay", "1,58,000.00")


def s_bank():
    return P.statement("Account statement", "Meridian Bank  |  ****4417  |  Sep 2026", [
        ("01 Sep   Salary credit", "+1,58,000.00"), ("03 Sep   Home loan EMI", "-34,200.00"),
        ("06 Sep   Northline Power", "-3,420.00"), ("07 Sep   Nimbus Fibre", "-1,299.00"),
        ("12 Sep   Green Basket", "-5,680.00"), ("16 Sep   Vertex Fuels", "-2,100.00"),
    ], "Closing balance", "2,41,318.00")


def s_rent():
    return P.statement("Leave and licence agreement", "11 Willow Road  |  page 1 of 6", [
        ("Licensor", "R. Menon"), ("Licensee", "Priya Sharma"),
        ("Term", "11 months from 01 Apr 2026"), ("Monthly licence fee", "Rs 34,000"),
        ("Security deposit", "Rs 2,04,000"), ("Notice period", "Two months"),
    ], "Registered on", "28 Mar 2026")


# ------------------------------------------------------------------ gallery
# A household's camera roll of paperwork: what was photographed on the kitchen
# table, plus what was scanned properly.
GALLERY = [
    (r_groceries, "photo", "wood"), (b_power, "photo", "desk"),
    (w_fridge, "photo", "cloth"), (r_fuel, "photo", "desk"),
    (p_health, "scan", None), (b_fibre, "photo", "desk"),
    (r_pharmacy, "photo", "wood"), (w_tv, "photo", "desk"),
    (s_salary, "scan", None), (r_dining, "photo", "cloth"),
    (b_water, "photo", "desk"), (p_motor, "scan", None),
    (r_school, "photo", "desk"), (w_washer, "photo", "wood"),
    (b_service, "photo", "desk"), (s_bank, "scan", None),
    (r_groceries, "photo", "desk"), (p_life, "scan", None),
    (s_rent, "scan", None), (b_power, "photo", "cloth"),
    (r_fuel, "photo", "wood"), (w_fridge, "photo", "desk"),
]
ok = 0
for i, (build, mode, surf) in enumerate(GALLERY):
    sheet = build()
    im = P.photographed(sheet, surf) if mode == "photo" else P.scanned(sheet)
    code, body = upload("/api/gallery/upload", f"IMG_{2400 + i}.jpg", P.jpeg(im))
    ok += code in (200, 201)
    if code not in (200, 201) and ok == 0:
        print("   gallery upload said:", code, body[:200])
print(f"  gallery: {ok}/{len(GALLERY)}")

for name in ("Bills 2026", "Warranties", "Policies"):
    call("/api/gallery/albums", {"name": name})
print("  albums: 3")

# ---------------------------------------------------------------- documents
DOCS = [
    ("Health policy — Sentinel", "Insurance", p_health),
    ("Car policy — Harbour General", "Vehicle", p_motor),
    ("Term life — Lumen", "Insurance", p_life),
    ("Salary slip — September", "Financial", s_salary),
    ("Bank statement — September", "Financial", s_bank),
    ("Rent agreement", "Property", s_rent),
    ("Fridge warranty", "Other", w_fridge),
    ("Television warranty", "Other", w_tv),
    ("Washer warranty", "Other", w_washer),
    ("Electricity bill — August", "Other", b_power),
    ("Car service invoice", "Vehicle", b_service),
    ("Broadband bill — September", "Other", b_fibre),
]
ok = 0
for title, cat, build in DOCS:
    blob = P.jpeg(P.scanned(build()))
    code, body = upload("/api/documents", f"{title}.jpg", blob,
                        extra={"title": title, "category": cat})
    ok += code in (200, 201)
    if code not in (200, 201) and ok == 0:
        print("   doc upload said:", code, body[:220])
print(f"  documents: {ok}/{len(DOCS)}")

st, g = call("/api/gallery")
n = len(g.get("photos", g if isinstance(g, list) else []))
print(f"\ngallery now reports {n} photos")
