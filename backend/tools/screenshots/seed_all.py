"""Seed the THROWAWAY instance properly, then it can be photographed.

Talks only to 127.0.0.1:8099 -- the SQLite instance with its own file and its
own media root. The live MySQL on 3307 is never touched: marketing images are
not worth one invented row in somebody's real financial records.

This replaces the earlier seeder, which was quietly wrong in four ways that all
showed up in the screenshots:

  * expenses were posted with `date`, but the column is `txn_date`, which falls
    back to TODAY -- so a month of spending stacked onto one day and the screen
    photographed as "Today: 17 items".
  * investments were posted with `invested`/`type`/`platform`; the fields are
    `invested_amount`/`invest_type`/`broker`. The values were dropped, so every
    holding read "invested Rs 0" and "+0.0%" next to a current value.
  * insurance was posted with `type` instead of `policy_type`, so every policy
    fell back to the default and displayed as "Other".
  * the vault and income were never seeded at all, so the vault screen
    photographed as its own empty state -- "No vault yet, 0 items".

Brand names here are invented. The previous set used real companies, which has
no place on a marketing page.
"""
import json
import random
import urllib.error
import urllib.request
from datetime import date, timedelta

B = "http://127.0.0.1:8099"
EMAIL, PW = "priya@example.com", "DemoHouse#2026"
TOKEN = None
random.seed(11)
today = date.today()


def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        B + path, data=data, method=method or ("POST" if data is not None else "GET"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:200]}


def d(days_ago):
    return (today - timedelta(days=days_ago)).isoformat()


def ahead(days):
    return (today + timedelta(days=days)).isoformat()


def post_many(path, rows, label):
    ok, first_err = 0, None
    for r in rows:
        st, body = call(path, r)
        if st in (200, 201):
            ok += 1
        elif first_err is None:
            first_err = (st, body)
    print(f"  {label}: {ok}/{len(rows)}" + (f"   first error {first_err}" if first_err else ""))
    return ok


st, r = call("/api/auth/login", {"email": EMAIL, "password": PW})
assert st == 200, r
TOKEN = r["token"]
print("signed in as", EMAIL)

# ------------------------------------------------------------------ income
# Without this the expenses screen shows "INCOME Rs 0" beside the spending,
# which reads as a half-finished app rather than a household ledger.
INCOME = [
    ("Salary", "Monthly salary — September", 186000, 2),
    ("Salary", "Monthly salary — August", 186000, 32),
    ("Freelance", "Design retainer", 24000, 9),
    ("Interest", "Savings interest", 3180, 14),
    ("Rent received", "Garage let", 6000, 5),
]
post_many("/api/expenses",
          [{"kind": "income", "category": c, "amount": a, "txn_date": d(days),
            "note": note, "method": "Bank transfer"} for c, note, a, days in INCOME],
          "income")

# ---------------------------------------------------------------- expenses
EXP = [
    ("Groceries", "Green Basket weekly order", 3240, 1),
    ("Fuel", "Petrol — Vertex Fuels", 2100, 2),
    ("Dining out", "Dinner, The Olive Room", 1860, 3),
    ("Groceries", "Vegetables, local market", 740, 4),
    ("Electricity", "Northline Power bill", 3420, 6),
    ("Internet", "Nimbus Fibre", 1299, 7),
    ("Medical", "Corner Chemist", 620, 9),
    ("Transport", "Cab to airport", 780, 10),
    ("Groceries", "Monthly staples", 5680, 12),
    ("Clothing", "Shirts", 2450, 14),
    ("Education", "School fees instalment", 18500, 16),
    ("Dining out", "Lunch with family", 1240, 18),
    ("Fuel", "Petrol — Vertex Fuels", 2000, 20),
    ("Maintenance", "Apartment association", 4200, 22),
    ("Subscriptions", "Streamly + Tunebox", 848, 24),
    ("Groceries", "Fruit and dairy", 1130, 26),
    ("Insurance", "Two-wheeler renewal", 3100, 28),
    ("Water", "CityWater quarterly", 1180, 30),
    ("Repairs", "Plumber — kitchen tap", 900, 33),
]
post_many("/api/expenses",
          [{"kind": "expense", "category": c, "amount": a, "txn_date": d(days),
            "note": note, "method": random.choice(["UPI", "Card", "Cash"])}
           for c, note, a, days in EXP],
          "expenses")

# ------------------------------------------------------------------ cards
post_many("/api/cards", [
    {"name": "Meridian Signature", "bank": "Meridian Bank", "last4": "4821",
     "limit": 500000, "outstanding": 18420, "due_date": ahead(11), "network": "Visa"},
    {"name": "Anchor Rewards", "bank": "Anchor Bank", "last4": "9036",
     "limit": 250000, "outstanding": 6250, "due_date": ahead(19), "network": "Visa"},
], "cards")

# ------------------------------------------------------------------ loans
post_many("/api/loans", [
    {"name": "Home loan", "lender": "Meridian Bank", "principal": 4200000,
     "outstanding": 3180000, "emi": 34200, "rate": 8.6, "start_date": d(1500),
     "tenure_months": 240},
    {"name": "Car loan", "lender": "Anchor Bank", "principal": 850000,
     "outstanding": 271000, "emi": 16400, "rate": 9.1, "start_date": d(760),
     "tenure_months": 60},
], "loans")

# -------------------------------------------------------------- insurance
# policy_type, NOT type -- the wrong key fell through to the "Other" default.
post_many("/api/insurance", [
    {"provider": "Sentinel Health", "policy_no": "SH-88421907", "policy_type": "Health",
     "premium": 28400, "sum_assured": 1000000, "frequency": "yearly", "renewal_date": ahead(38)},
    {"provider": "Lumen Life", "policy_no": "LL-4471203", "policy_type": "Life",
     "premium": 21600, "sum_assured": 10000000, "frequency": "yearly", "renewal_date": ahead(96)},
    {"provider": "Harbour General", "policy_no": "HG-2209417", "policy_type": "Motor",
     "premium": 14200, "sum_assured": 900000, "frequency": "yearly", "renewal_date": ahead(12)},
], "insurance")

# ------------------------------------------------------------ investments
# invested_amount / invest_type / broker.
post_many("/api/investments", [
    {"name": "Broad Market Index Fund", "invest_type": "Mutual fund", "broker": "Kite Invest",
     "invested_amount": 420000, "current_value": 518400},
    {"name": "Public Provident Fund", "invest_type": "PPF", "broker": "Meridian Bank",
     "invested_amount": 750000, "current_value": 812000},
    {"name": "Flexi Cap Fund", "invest_type": "Mutual fund", "broker": "Growvest",
     "invested_amount": 260000, "current_value": 337900},
    {"name": "Sovereign Gold Bond", "invest_type": "Bonds", "broker": "National Savings",
     "invested_amount": 180000, "current_value": 224600},
    {"name": "Fixed Deposit — 3 yr", "invest_type": "FD", "broker": "Anchor Bank",
     "invested_amount": 300000, "current_value": 331500},
], "investments")

# ---------------------------------------------------------------- vault
# The vault photographed as its own empty state, which is the single worst
# thing a product screenshot can be.
post_many("/api/vault", [
    {"title": "Electricity board login", "username": "priya.sharma",
     "url": "northline-power.example", "category": "Utilities", "password": "9dK!ru28Qm"},
    {"title": "Broadband account", "username": "priya@example.com",
     "url": "nimbus-fibre.example", "category": "Utilities", "password": "Tt4$wenzo1"},
    {"title": "Tax filing portal", "username": "AKXPS4417L",
     "url": "incometax.example", "category": "Government", "password": "Zx7#plmQ42"},
    {"title": "Property registration", "username": "priya.sharma",
     "url": "registry.example", "category": "Property", "password": "Bn2@kdlW90"},
    {"title": "School parent portal", "username": "arjun.parent",
     "url": "school.example", "category": "Family", "password": "Qw9!zbrT31"},
    {"title": "Wi-Fi — home", "username": "SafeNest-5G",
     "category": "Home", "password": "kitchen-window-42"},
    {"title": "Locker PIN — bank", "username": "Locker 214",
     "category": "Bank", "password": "7712"},
], "vault")

# -------------------------------------------------------------- reminders
post_many("/api/reminders", [
    {"title": "Car insurance renewal", "due_date": ahead(12), "notes": "Harbour General"},
    {"title": "Electricity bill", "due_date": ahead(4), "notes": "Northline Power"},
    {"title": "School fees — term 3", "due_date": ahead(21), "notes": "Instalment 3 of 3"},
    {"title": "Passport renewal", "due_date": ahead(64), "notes": "Both adults"},
    {"title": "Fridge service", "due_date": ahead(31), "notes": "Under warranty until 2028"},
], "reminders")

# ------------------------------------------------------------------ todos
post_many("/api/todos", [
    {"title": "Scan the new rent agreement"},
    {"title": "Add Arjun's vaccination card"},
    {"title": "Check the gas connection transfer"},
    {"title": "Back up to the external drive"},
], "todos")

print("\nledger seeding done")
