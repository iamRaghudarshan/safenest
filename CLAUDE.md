# SafeNest — project guide

Read this first. It is written so that someone — or another Claude session — who
has never seen this codebase can pick it up and be productive without asking
anything.

**If you are setting this up on a new machine, read [§5](#5-moving-this-to-another-computer)
before anything else.** The folder does not contain the database or the database
server, so copying it alone gives you a working app with no data in it.

> **Name note.** The product is now called **SafeNest**. The folder, the Python
> package, the database and dozens of internal identifiers still say `finmate`.
> That is deliberate — see [§9 Branding](#9-branding-the-name-and-icon-are-data). Do not
> rename files or database columns to "fix" it.

---

## 1. What this is

A private personal-finance and life-records app. One person's money, documents,
photos and passwords in one place, running **on their own computer** — not on a
server anyone else controls. It is sold as a **licensed copy**, not a subscription.

Ten modules: Expenses, Loans, Cards, Insurance, Investments, Documents, Photos
(Gallery), Vault, Reminders, To-dos. Plus a dashboard, unified search, activity
log, notifications and an admin area.

The whole product argument rests on one property: **customer records never leave
the customer's machine.** Every design decision below follows from it. If you are
about to add something that uploads user data anywhere, stop and reconsider.

---

## 2. Stack and versions

| Part | What | Version on the original machine |
|---|---|---|
| Frontend | React 19 + TypeScript + Vite, PWA | Node 20.19.4, npm 10.8.2 |
| Backend | FastAPI + SQLAlchemy 2 | Python 3.13.5 |
| Database | MySQL 8.4 **on port 3307**, or SQLite | MySQL 8.4.6 |
| Auth | PyJWT (HS256) + bcrypt | — |
| Crypto | `cryptography` — AES-256-GCM, Ed25519 | — |
| AI | onnxruntime: OpenCV YuNet + SFace, CLIP ViT-B/32, rapidocr 3.9.2 | — |
| Tunnel | cloudflared named tunnel | — |

**Port 3307 is not a typo.** A separate MySQL 8.4 instance runs there for this app.
The machine also has a stock `MySQL84` service on 3306 that is **a different
instance and must be left alone**.

---

## 3. Layout

```
finmate-react/
├── backend/
│   ├── app/
│   │   ├── main.py          FastAPI app, migrations, licence gate, SPA mount
│   │   ├── config.py        settings from backend/.env — no defaults for secrets
│   │   ├── models.py        every SQLAlchemy table
│   │   ├── database.py      engine + SessionLocal
│   │   ├── security.py      hashing, JWT, require_admin, per-module guards
│   │   ├── crypto.py        AES-256-GCM vault encryption + key rotation
│   │   ├── signing.py       HMAC-signed, expiring media URLs
│   │   ├── licensing.py     Ed25519 licences — issue, parse, evaluate, poll
│   │   ├── bundler.py       builds the portable/licensed copies
│   │   ├── ocr.py           reads text off documents and photos
│   │   ├── vision.py        CLIP embeddings; albums_auto.py clusters faces
│   │   ├── ist.py           the single clock (IST, UTC+05:30)
│   │   └── routers/         one module per feature area
│   ├── venv/                Python virtualenv (not in git)
│   ├── models/              ~190 MB of ONNX weights (not in git)
│   └── .env                 ALL SECRETS (not in git)
├── frontend/
│   ├── src/
│   │   ├── branding.ts      app name + icon store — read this before renaming
│   │   ├── api.ts           fetch wrapper, JWT, typed ApiError
│   │   ├── screens/         one file per screen
│   │   └── index.css        the whole design system
│   ├── public/sw.js         service worker
│   └── dist/                built SPA — the backend serves this
├── bundle/                  what gets copied into a portable copy
│   ├── setup.py             the installer that runs on the customer's machine
│   ├── wizard.py            its tkinter GUI
│   └── Start App (…)    launcher TEMPLATES — renamed at build time
├── cloudflared/config.yml   tunnel ingress
└── *.bat / *.ps1            start/stop/install helpers
```

---

## 4. Getting it running

### Order matters: MySQL → backend → tunnel.

```bash
# 1. MySQL on 3307 (its config lives outside the repo)
"D:\AI PRO\tools\mysql-8.4.6-winx64\bin\mysqld.exe" --defaults-file="D:\AI PRO\tools\my.ini"

# 2. Backend
cd backend
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8080 \
    --no-access-log --no-server-header

# 3. Frontend (dev)
cd frontend && npm run dev          # Vite on 5173, proxies /api to 8080
npm run build                       # writes dist/, which the backend serves
```

`--no-server-header` matters: without it uvicorn advertises its version.

**On a new machine you must create `backend/.env` yourself.** There are no
in-code defaults for secrets — the app refuses to boot without them, on purpose.
Required keys:

```
DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD   # or DB_ENGINE=sqlite
JWT_SECRET            >= 32 chars
VAULT_KEY_HEX         exactly 64 hex chars (32 bytes)
MEDIA_SECRET          >= 32 chars
LICENSE_SIGNING_KEY_HEX  publisher only — see §8
LICENSE_PUBLIC_KEY_HEX
PUBLIC_BASE_URL
VAPID_PUBLIC_KEY VAPID_PRIVATE_KEY VAPID_SUBJECT   # optional, enables push
CF_API_TOKEN CF_ZONE_ID CF_ACCOUNT_ID              # optional, per-customer subdomains
```

Generate secrets:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET, MEDIA_SECRET
python -c "import secrets; print(secrets.token_hex(32))"       # VAULT_KEY_HEX
```

`config.py` keeps a `_BURNED` set of secrets that shipped in old builds and
**refuses to start** if it sees one. Do not remove that check.

### Make it survive a reboot

Everything above dies with its shell. `Install App Services.bat`
(**run as administrator**) registers MySQL as a service, the API as a
boot-triggered scheduled task, and cloudflared as a service. Without it, a sleep
or a closed terminal takes the app down — this has happened repeatedly.

---

## 5. Moving this to another computer

**The folder alone is not enough.** It carries the code and the uploaded photos and
documents, but *not the database*, not the database server, and not the tunnel.
Copy only this folder and you get a working app with nothing in it.

### Inside the folder (~1.3 GB)

- source, `backend/models/` (188 MB of ONNX weights), `backend/private/` (uploaded
  photos and documents), `backend/.env`, `frontend/dist/`
- `backend/venv/` (562 MB) and `frontend/node_modules/` (87 MB) — **rebuild these,
  do not trust the copy.** They hold absolute paths and platform-specific binaries
  and will not work on another OS.

### NOT inside the folder

| Thing | Where it actually lives | Why it matters |
|---|---|---|
| **The database** | `D:/AI PRO/tools/mysql-data/finmate` | every record you have. Without it the app starts empty |
| MySQL config | `D:/AI PRO/tools/my.ini` | defines port 3307 and the datadir |
| MySQL server | `D:/AI PRO/tools/mysql-8.4.6-winx64/` | the binaries |
| cloudflared | `C:\Program Files (x86)\cloudflared\` | the tunnel binary |
| Tunnel credentials | `%USERPROFILE%\.cloudflared\` | without these the public address never comes up |

### What to copy — the short answer

Copy **`finmate-react/` and part of `tools/`**. They are siblings under `D:\AI PRO`.

| Copy | Size | Why |
|---|---|---|
| `finmate-react/` | 1.3 GB | the app |
| `tools/mysql-data/` | 264 MB | **the database — every record** |
| `tools/my.ini` | 1 KB | defines port 3307 and the datadir |
| `tools/mysql-8.4.6-winx64/` | 991 MB | the MySQL server. Skip it if you would rather install MySQL 8.4 fresh on the new machine |

**≈ 2.6 GB total.** Copying all of `D:\AI PRO` (3.4 GB) also works and is simpler —
it just brings unrelated projects with it.

Safe to leave behind (~880 MB of `tools/`): `dl/` (download cache), `php/`,
`ci-tmp/`, and `faceenv/` + `faceservice/` — that external face service is dead
code. `face_service_url` appears in `config.py` and nowhere else; face matching
runs in-process from `backend/models/`.

### Five files hold absolute paths — repaired automatically

The new machine's drive is rarely `D:`, so these would otherwise need hand-editing
on every move. **Run `Fix Paths After Moving.bat`** (or `fix-paths.ps1`) once after
copying. It works out where everything now is from its own location and rewrites
all five. It is idempotent — run it as often as you like — and
`install-services.ps1` calls it automatically, so installing the services on a new
machine already does the right thing.

Tested by copying the config files to a `C:` path and running it: all five
repaired, `port=3307` untouched, and a second run reported "Nothing needed
changing".

What it fixes, and what you would otherwise edit by hand:

| File | What to change |
|---|---|
| `tools/my.ini` | `basedir`, `datadir`, `log-error` |
| `finmate-react/install-services.ps1` | lines ~60–61: `$mysqld`, `$myini` |
| `finmate-react/uninstall-services.ps1` | line ~14: `$mysqld` |
| `finmate-react/start-finmate-react.ps1` | line ~7: `$root` |
| `finmate-react/cloudflared/config.yml` | `credentials-file` — points at `C:\Users\<you>\.cloudflared\`, so it changes with the **user**, not just the drive |

`backend/.env` contains no absolute paths, so it needs no edits for a move — only
if the database host, port or password differ.

### Manual move

1. Copy the folders listed above.
2. **Run `Fix Paths After Moving.bat`.** Nothing below will work until the paths
   point at the new location.
3. **Bring the database.** With MySQL stopped, copy the whole `mysql-data`
   directory — or, safer across versions, take a dump:
   `mysqldump --port=3307 -u finmate -p finmate > finmate.sql`
4. Install MySQL 8.4 on the new machine, place `my.ini`, point `datadir` at the
   copied data (or import the dump), and start it **on 3307**.
5. Rebuild the environments rather than copying them:
   ```
   cd backend  && python -m venv venv && venv\Scripts\pip install -r requirements.txt
   cd frontend && npm install && npm run build
   ```
6. Check `backend/.env` — host, port and password must match the new machine.
7. Install cloudflared and copy `~/.cloudflared/` if you want the public address;
   otherwise clear `PUBLIC_BASE_URL`.
8. Start in order: **MySQL → backend → tunnel**.

### The supported path is easier

Profile → **Move everything to another computer** builds a self-contained folder
holding a SQLite database, the media and an installer that configures the far end.
It avoids every step above. Use the manual route only when you specifically want a
MySQL setup on the new machine.

### Before handing this folder to anyone

`backend/.env` travels with it and contains **`LICENSE_SIGNING_KEY_HEX`** — the
private Ed25519 half. Anyone holding it can mint licences for your product and the
whole scheme becomes decorative. It also carries the vault key, the JWT and media
secrets and the Cloudflare token.

Never send this folder to a customer. Use the licensed build (§8), which strips
every one of those. Sending it to another *developer* means handing over the keys
to the business — rotate them afterwards if that was not intended.

---

## 6. Database

Dual dialect. `DB_ENGINE=mysql` (default) or `sqlite` — the portable copies use
SQLite so the whole app is one folder.

Migrations live in `main.py::_migrate()` and are **idempotent top-ups**, not a
migration framework:

- **SQLite** → `Base.metadata.create_all()` and stop. A SQLite file is either brand
  new or was written by this same version.
- **MySQL** → a list of `(table, column, DDL)` tuples, each applied only if
  `information_schema` says the column is missing, followed by
  `Table.__table__.create(checkfirst=True)` for whole new tables.

**To add a column:** append to the `stmts` list *and* add it to the model. To add a
table: add the model and a `create(checkfirst=True)` line. Never edit an existing
entry — installs that already ran it will not re-run it.

---

### A macOS .app puts the payload in Contents/Frameworks

Not `Contents/MacOS/_internal`. `Contents/MacOS` holds the executable and nothing
else, so anything that looks for `_internal` beside the executable finds nothing:

```
Contents/MacOS/     <the executable>, version.txt          <- just these
Contents/Frameworks/   454 entries                <- the payload
```

`updates.install_root()` did exactly that and returned `None`, so `/api/update`
reported `installable: false` and the update row **never rendered**. A customer
reported "I clicked and there was no action" about a button that was not on the
screen at all — and the self-update system could not deliver its own fix, because
the broken part was the updater. That copy needed a manual reinstall.

The unit an update replaces on macOS is the **whole .app**, never pieces of it: a
bundle is signed as one thing. `install_root()` walks up to the `.app`, and the
swap uses `ditto` (which preserves symlinks and the extended attributes the
signature lives in) rather than `cp -R`.

**Records are never inside the .app.** They live in
`~/Library/Application Support/<Brand>/`, because writing into a bundle breaks its
signature and anything in there is destroyed by a drag to Applications or an
update. The licence travels read-only in `Contents/Resources/seed-data` and
`runner.py` copies it out on first run.

### Updating a copy that is already in someone's hands

**The data-safety half came first, because it was already broken.** `_migrate()`
did `create_all()` and nothing else for SQLite, on the reasoning that such a file
is "either brand new or written by this same version". Shipping updates destroys
that assumption: `create_all()` creates missing **tables** and never missing
**columns**, so a customer updating to a build with a new field would keep the old
table and every query touching it would fail with *no such column* — their records
present and unreadable, the worst shape a data problem takes.

`_sqlite_topup()` now derives the missing columns **from the models**, so anything
added to `models.py` reaches existing customer databases without a hand-kept list
to forget. `_sqlite_pending()` checks first so a launch with nothing to do copies
nothing, and `_backup_sqlite()` takes a copy (with `-wal`/`-shm`, or the newest
writes are missing) before altering anything. Columns are added NULLable whatever
the model says — SQLite cannot add NOT NULL without a constant default, and a
nullable column the app then fills is recoverable where a refused migration is not.
A column *with* a default is backfilled by SQLite, which is why `seats` reads 1 on
old rows and agrees with `seats_allowed()`.

Verified against a database rebuilt to look like an older build's: columns added,
new tables created, every row still there, backup holds them too, and a second run
changes nothing.

**`updates.py` is the delivery half**, and it is signed the same way licences are:
a release manifest is an Ed25519 statement of version, size and SHA-256. HTTPS says
the bytes arrived unaltered from whoever answered, not that it was us who answered
— and an update replaces the program that reads someone's whole financial history.
Refused: a manifest signed by another key, an edited version number, a download
whose checksum differs, and a zip with an entry pointing outside its folder.

**Only the program is replaced.** `data/` is never touched, the download is
unpacked *outside* the app folder, and the swap script excludes `data`. Windows
will not let a running program overwrite its own `.exe`, so the app writes a
script, starts it and exits; the script waits for the process to go, swaps the
files and relaunches.

**Nothing downloads by itself.** `releases.py` is the publisher half (package the
built folder, release it to everyone, which also posts an announcement so the
prompt is not a surprise); `household.py::updater` is the customer half, and it
only acts when someone presses the button. An app that replaces itself unasked is
what people are rightly taught to refuse.

## 7. Security model

This is the product, not a checklist. A full VAPT/SAST/DAST pass was done; keep it
that way.

| Concern | How |
|---|---|
| Vault contents | AES-256-GCM (`crypto.py`), key from `VAULT_KEY_HEX`, never leaves the machine |
| Passwords | bcrypt; login compares against a dummy hash on miss so timing cannot enumerate emails |
| Sessions | JWT HS256; `users.token_version` is bumped on password change, killing every existing token |
| Photos/documents | **Never** served by a static mount. `/api/gallery/media/{name}` checks an HMAC signature bound to the owner and an expiry (`signing.py`) |
| Licences | Ed25519, verified offline on the customer's machine |
| Rate limits | `ratelimit.py` on every public unauthenticated endpoint |
| Headers | `Cache-Control: no-store` on all `/api/*`; HSTS when the forwarded scheme is https; `Server:` carries the app name and replaces uvicorn's banner |

**Never re-add a `StaticFiles` mount over the media tree.** It would make every
photo world-readable to anyone who guesses a filename.

**What must never appear in a customer's copy:** the vault key, the licence
*signing* key, VAPID keys, tunnel credentials, or anyone else's records. The
bundler strips all of them; there are tests for this.

---

## 8. Licensing

Two roles, and **one build must never hold both**:

- **Publisher** — this installation. Holds `LICENSE_SIGNING_KEY_HEX` (the private
  Ed25519 half) and can mint licences.
- **Customer** — a copy handed to someone else. Holds only the public key, its own
  `licence.key`, and `LICENSED_MODE=true`.

`config.is_publisher` is simply "is the signing key set".

### Perpetual licences and seats

**`days=None` issues a licence that never expires.** It carries an explicit
`perpetual: true` rather than simply omitting `expires`: a token that has merely
*lost* its expiry must stay INVALID, and "sold outright" is not something to infer
from an absence. `evaluate()` short-circuits on the flag before any date
arithmetic; `expires_on` is NULL in the row. A perpetual licence can still be
**withdrawn** — it just never lapses on its own.

`parse()` rejected anything without `expires`, so the first perpetual token came
back INVALID. It now accepts either, and rejects a payload with neither.

**`seats` is how many sign-ins the customer's household may have.** 0 is
unlimited. It lives inside the signed token, so a customer cannot raise their own
limit — verified by editing `seats` and `perpetual` in a real token and watching
both fail the signature.

`seats_allowed()` reads a **missing** `seats` as **1**, not unlimited. Licences
already in the field have no such key, and 1 is exactly what those copies can do
today. Defaulting to unlimited would silently widen every licence ever issued.

**`routers/household.py`** lets the licence holder add family members. Same
reasoning as the web address in §10: a licensed copy has no administrator by
design, so `require_admin` would put this behind a role nobody there ever has.
Members are always created as `user` — a household member must not become the
exception that quietly reintroduces an admin. Removing the last sign-in, or your
own, is refused: there is no administrator anywhere in a licensed copy to rescue
it from having no accounts.

`payload["expires"]` in the issue endpoint's audit line raised KeyError on the
first perpetual licence — a 500 *after* the row was written, so it was issued,
recorded, and reported as a server fault. It is `.get(...,` "never"`)` now.

### States

`licensing.py`: `OK / EXPIRING / GRACE / EXPIRED / REVOKED / INVALID / MISSING`.
`GRACE_DAYS = 3`. `BLOCKING = {EXPIRED, REVOKED, INVALID, MISSING}`.

The `licence_gate` middleware in `main.py` returns **402** when blocked. During
GRACE, reads work and writes are refused.

`_LICENCE_OPEN` stays reachable when blocked: health, login, `/api/auth/me`,
licence status/activate, and `/api/branding` (the lapsed-licence screen still has
to show the app's own name).

### Export is never blocked — `_DATA_OUT`

`/api/system/export` returns **before** the blocking check, so it works in every
state: expired, withdrawn, corrupt licence, no licence at all.

A lapsed licence ends someone's right to *use* the software. It does not make
their records ours to withhold, and holding them hostage over a renewal is not a
position to be in. This used to be a GRACE-only exemption, which meant that three
days after expiry a customer who simply forgot to renew could no longer get their
own data out. The prefix covers the whole flow — POST to start, GET to poll, the
history list — and it is still behind authentication, so this lets the signed-in
owner take their own data, not a stranger.

**It is not a bypass, and that was checked rather than assumed.** The exported
copy carries the customer's *own* licence, so it is blocked exactly as the
original was. Verified by tampering with `licence.key`, exporting from the blocked
copy, then launching the export: it runs, and `/api/expenses` still returns 402.

### A customer's copy exports by cloning itself

`bundler.build()` assembles a bundle from the source tree — `backend/`,
`frontend/dist`, `bundle/setup.py`. **A customer's copy has none of those**, so
export there failed with *"frontend/dist is missing — build the web app first"*
in every licence state. The one feature that gets someone's records out of the
app did not work for the people who most needed it, and nothing surfaced that
until the packaged build was actually run.

`bundler.build_installed()` handles it: `installed_root()` detects a frozen build
(`sys.frozen`, with `_internal` beside the executable), and the copy is made from
the installation itself rather than from sources the customer was never given.
The data half is shared with `build()` — `userexport`, `_copy_media`,
`_write_carried_secrets` — so a personal export is still a strict slice of that
one user with the vault re-encrypted under a fresh key. `system.py::_run_export()`
picks the path; the source bundle stays the publisher's route.

It **refuses to export for the other platform**. The copy is made of the binaries
on this machine, so a Windows copy asking for a Mac bundle would produce a
Mac-named folder full of Windows executables — a failure discovered only after
someone carried it to the other computer.

`userexport` deliberately promotes the exported account to **admin** ("on their
own computer they are the only account"). For a licensed customer that means
exporting and reopening yields an admin of their own copy. That is not an
escalation — the gate is machine code and still enforced, and no signing key
travels — but it does mean §10's "a customer's copy has no administrator at all"
holds only until they export.

### Nothing of the publisher's may appear in a customer's copy

`config.py`'s `public_base_url` **must stay `""`**. It was hard-coded to
`https://finmate.raghudarshan.online`, so every customer copy showed the
publisher's private domain on their own Profile screen as "your web address" — on
their computer, twice. Because it was a default compiled into the build, no
launcher fix could override it. Found on a real customer install, not here.

`runner.py` made it worse by copying `licence.json`'s `check_url` into
`PUBLIC_BASE_URL`. That is the **publisher's** address, and it is not needed there
at all: `licensing.check_revoked()` takes the issuer from inside the *signed*
token. It now writes `LICENSE_CHECK_URL`, which is what `bundle/setup.py` always
did correctly.

`/api/licence/status` no longer returns `reports` / `reports_to` either. Listing
the machine details a copy sends, next to the supplier's address, was meant as
candour and read as surveillance — customers took "sent … once a day" for the app
reporting on them. That belongs in the licence terms, not in Settings.

**Before every release, grep a built customer copy for your own domain and for the
brand token.** Both are checked in the smoke script; both had leaked.

### The polling loop — expiry-driven, not clock-driven

A licensed copy still re-checks (a withdrawal has to reach a machine left running
for weeks — that was a real failure once), but the cadence follows the licence
rather than the clock. `main.py::_wait_seconds()`: once at startup, then silence
until the licence is within `licence_watch_days` (7) of expiry, then every
`licence_poll_minutes` while it matters.

| Days left | Next network check |
|---|---|
| 365 | 358 days |
| 28 | 21 days |
| 8 | 1 day |
| ≤ 7 | 15 minutes |

It used to ask **every 15 minutes for the licence's whole life**. Expiry itself
needs no network at all — the date is inside the signed licence and is evaluated
offline on every request. The trip exists only to hear about a withdrawal.

**The trade-off, stated plainly:** withdrawing a licence mid-term now takes effect
when that copy restarts, or when it comes within a week of expiry — not within
fifteen minutes. Announcements ride the same trip and arrive on the same schedule.

### The polling loop — do not remove it

A customer copy checks its licence **every `licence_poll_minutes` (default 15)**,
not just at startup. This was a real bug once: suspension never took effect
because the licence was read once at boot, so a suspended copy kept working
indefinitely while online. `main.py::_check_licence()` loops.

`check_revoked()` **fails open** — if the publisher's server is unreachable, the
customer keeps working. That is deliberate: our downtime is not their problem.

### Broadcasts and receipts

Delivery to customers is **pull-only** — you cannot push to a machine you do not
run. Copies poll `/api/licence/announcements/{key_id}` with a `since` high-water
mark and turn each message into a local notification.

Two consequences that bit us, both now handled:

1. **A resend must be a new row.** A copy only ever asks for ids *above* the
   highest it has stored, so re-flagging an old row can never reach it.
2. **`_newest_of_each()`** collapses a message and its resends at delivery time.
   Without it, a copy that never collected the original gets both it *and* the
   resend — worst for exactly the person the resend was chasing.

`broadcast_receipts` records who actually collected what, so the admin list can
tell "never opened their copy" from "read it days ago". Those look identical
otherwise, and mean opposite things.

---

## 9. Branding: the name and icon are data

**This is how you rename the app. Do not edit source to do it.**

Admin → Profile → **Administration → App name and icon**. Set the name, short
name, tagline, and upload an icon. One upload is rendered server-side into 32,
180, 192 and 512 px (`branding.py::SIZES`). Non-square images are **padded, not
cropped**, so a wide wordmark keeps both ends.

Where it reaches:

- `GET /api/branding` — **public, unauthenticated** (the login screen needs the
  name before anyone signs in) and rate limited at 120/min
- `GET /manifest.webmanifest` — **generated**, registered *before* the SPA mount so
  it wins over the compiled file. Without this, home-screen shortcuts would keep
  the old name for ever
- `GET /` and `/index.html` — **rewritten** by `main.py::_branded_index()`, also
  registered ahead of the SPA mount. The compiled `index.html` carries the
  build-time name in `<title>` and `apple-mobile-web-app-title`, and the browser
  reads both *before* any JavaScript runs — so a renamed app flashed the old name
  in the tab on every cold load, and an iPhone home-screen shortcut kept it
  permanently. `theme-color` is substituted too. Falls back to the file unchanged
  if the branding lookup fails: a missing name must never cost someone their app
- `GET /branding/icon-{size}.png` and `/favicon.ico`
- Frontend `branding.ts` sets `document.title`, favicon, apple-touch-icon,
  theme-color and the manifest link
- `useBranding()` in components; `appName()` for prose and plain modules
- Backend: the daily push title (`digest.py`) and the licence-gate message
  (`main.py::_app_name()`)
- **Bundles**: folder becomes `<Name>-for-Windows`, launcher becomes
  `Start <Name> (Windows).bat`, and `setup.py` / `wizard.py` / `README.txt` are
  rebranded as they are copied

### `branding.app_name()` — use it for any string a person reads

The name was hard-coded in a dozen places the branding screen could not reach, so
a rename produced a half-rebranded copy: the login screen said the new name and
the push notification that arrived an hour later said the old one. Those are now
all `routers/branding.py::app_name(db=None)` — takes a session if the caller has
one, opens its own if not, and **never raises**, because every caller is either a
notification or an error message and neither may fail over the app's own name.

Now dynamic: push titles (`notifications.py`), the export-ready and export-failed
alerts (`system.py`), "this is not a X licence" (`licensing.py`), "this copy
cannot issue licences" (`licences.py`), the Cloudflare DNS record comment
(`cftunnel.py`), the `Server:` header and the OpenAPI title (`main.py`), and the
console the customer sees at launch (`packaging/runner.py::brand()`).

**`app_name_cached()` for hot paths only.** The `Server` header is set on every
response; a database round-trip per response for a header is absurd. `update()`
calls `forget_name()` after committing, so a rename still takes effect without a
restart — verified by renaming through the real API and watching the header, the
manifest and the licence message all follow, then restoring.

Two deliberately **not** dynamic:
- `config.py`'s configuration-error banner says `[config]`. It fires before the
  database is open, and the name lives in the database. Naming the file that needs
  fixing is more use to whoever is reading it.
- `bundler.py`'s `COMPILED_DIR`, `App.exe` and the launcher templates. Those
  are the template *filenames* the bundler looks for and renames per customer.

### The one rule that makes bundle rebranding safe

`bundler.BRAND_TOKEN = "App"` is replaced **case-sensitively** at build time.
Every *functional* string in the bundle scripts is lower case — `finmate.db`,
`finmate-config.json`, the MySQL user — so the swap renames all the wording and
touches none of the plumbing. **If you add a new user-visible string to
`bundle/setup.py` or `wizard.py`, write the brand as `App` (capitalised) and it
will be renamed automatically. Never introduce a capitalised `App` as a
filename, path or dict key.**

`_display_name()` strips characters illegal in filenames and caps at 40 chars;
a blank name falls back to the default.

**Licensed copies carry the branding.** The licensed bundle ships a deliberately
empty database, so `_carry_branding()` copies the branding row and the icon files
in explicitly. Verified: the branding row travels and **zero** rows from any other
table do.

`frontend/src/branding.ts` has a `FALLBACK` constant used before the server
answers. `api.ts` deliberately does **not** import `branding.ts` — that would be a
circular import, so its error messages are worded without a product name.

---

## 10. The web address is data too

Same idea as branding: **Profile → Administration → 🌐 Web address**. Set the
domain, paste the Cloudflare tunnel id and token, write the tunnel config, and
test that the address really reaches this server.

`PUBLIC_BASE_URL` in `.env` is now only a **fallback**. Everything reads
`weburl.public_url(db)`, which returns the stored value when there is one and the
`.env` value otherwise — so an installation that never opens this screen behaves
exactly as it always did. The screen says "currently using … from the
configuration file" when that is what is happening.

### Who may change it — and why it is not admin-only

`hosting.can_manage()`: an **administrator always**, and in a **licensed copy any
signed-in user**.

That second half is not a loosening, it is the whole point. A licensed customer is
given the `user` role deliberately — they administer nothing of the publisher's —
so a customer's copy has **no administrator at all**. Gating the web address on the
admin role would have put it behind a role that, by design, nobody in a customer's
copy ever has: the feature would exist and be permanently unreachable for exactly
the people it is for.

On the publisher's own installation `licensed_mode` is off and several people may
share it, so it stays admin-only. Verified both ways: plain user here gets **403**,
and a real licensed copy running with `LICENSED_MODE=true` lets its `user` set both
the domain and the tunnel id.

The Profile entry therefore sits in its own **"Reaching this app"** group, not
inside Administration, and `WebAddressSection` asks the server for `can_manage`
rather than checking the role itself. Keep that arrangement — deciding permission
in the frontend is how the two halves drift apart.

### One token instead of four terminal commands

`POST /api/hosting/auto` does steps 4–7 — create the tunnel, tell it where to
route, point the DNS record at it, start the connector. The customer pastes a
Cloudflare API token and a domain; nothing else.

**The trick that removes the terminal:** a tunnel created with
`config_src: "cloudflare"` keeps its ingress at Cloudflare, so the connector runs
from a token alone — `cloudflared tunnel run --token <TOKEN>`. No
`tunnel login`, no cert.pem, no credentials JSON, no `config.yml`.
`tunnelrun._command()` prefers the stored token and falls back to the config file
for installations set up by hand.

**The API token is used and dropped.** Never stored, never returned to the
browser. It can edit DNS and create tunnels across the whole zone; the tunnel
token it produces is scoped to one tunnel and is the only thing worth keeping.

Steps 1–3 stay manual because they cannot be otherwise: nobody but the owner can
add a domain to their Cloudflare account or change nameservers at their registrar.
The manual steps are still there, folded into a `<details>` — someone whose token
cannot be created must not be stuck.

`/auto/check` exists so the two failures people actually hit are reported **before
anything is created**: a token for the wrong Cloudflare account (the error lists
the zones the token *can* see), and a zone whose `status` is not `active`, which
means the nameservers have not switched over yet. `setup()` reuses a tunnel of the
same name rather than making a second one, and `upsert_dns` replaces rather than
adds — both matter because people re-run this after a half-working first attempt.

### It is a walkthrough, not a form

The first version of this screen asked for a tunnel id — which is the *last* step
of a process nothing on screen explained. It now walks through all nine steps: buy
a domain, add it to Cloudflare, switch the nameservers, install the connector, log
in, create the tunnel, route DNS, save, restart, test.

**Every command is built from what the person has typed**, so there is never a
`<placeholder>` left to substitute. Type `finmate.example.com` and step 7 already
reads `cloudflared tunnel route dns <their-id> finmate.example.com`. Each command
has a copy button, because these get mistyped otherwise. The tunnel name in step 6
comes from `appName()`, so a renamed app suggests a matching tunnel name.

If you add a step, keep it in the same shape: a real instruction naming the actual
buttons on the far side ("choose **Add a site**", "find the **Nameservers**
setting"), not a summary of what the step is for.

`weburl.normalise()` accepts what a person would actually type
(`finmate.example.com`, `https://Finmate.Example.com/`) and always returns
`https://…`; a plain-http address would send the licence key in the clear. It
refuses a port, because the tunnel handles that.

### The trap this screen must keep warning about

**Changing the address does not move licences already issued.** The issuer is
inside a signed Ed25519 token and cannot be rewritten. Those copies keep calling
the old address for ever. So the endpoint counts the live licences and returns
`licences_on_old_url`, and the screen says: keep the old address working, or issue
those licences again. Do not remove that warning.

### Why it does not restart the tunnel

Writing `~/.cloudflared/config.yml` is safe and reversible. Stopping a Windows
service from inside a web request is neither — and it would cut off the very
connection the request arrived on. The screen writes the file and tells the person
to run `net stop cloudflared && net start cloudflared`.

### `/api/hosting/check` compares a build marker, not just the status code

A domain that resolves to somebody else's site, or to an **older copy of this app
on another machine**, answers `200` perfectly happily. The check reads
`/api/health` and confirms `service == "finmate-api"`, because that second failure
is the one worth catching: everything looks fine from the outside.

Tunnel tokens are never returned to the browser in full — only `first6…last4` and
a length. Sending an empty token means "keep the stored one", since the screen
cannot round-trip a value it was never given.

---

## 11. Protecting the code in a customer's copy

```bash
python packaging/build_exe.py --native            # machine code, for customers
python packaging/build_exe.py                     # bytecode only, faster
```

`--native` runs **Nuitka** over `backend/app` first, producing a single
`app.cp313-win_amd64.pyd` (~4.4 MB) of machine code, which PyInstaller then
bundles. Nuitka is a **build-time** dependency and is deliberately NOT in
`requirements.txt` — that file is installed on customers' machines by `setup.py`.

    backend\venv\Scripts\pip install nuitka

It compiles with **ziglang**, which Nuitka downloads itself (~50 MB). No Visual
Studio Build Tools needed.

### What each build actually exposes

| Build | Your source | Your bytecode | Editable? |
|---|---|---|---|
| Before any of this | **2,214 `.py` files** | — | yes, in Notepad |
| Default | none | in the PYZ archive | only via a decompiler |
| `--native` | none | **none** | no — machine code |

Verified on the `--native` build: `0` source files, `0` app modules in the PYZ,
and the `.pyd` present. It runs — it creates `finmate.db` with an 800 KB WAL,
which only happens if `app.models` and `app.database` loaded and built the schema.

### Four traps, all hit for real

**1. `datas` shipped the source.** The spec listed `backend/app` as a *data*
directory, so PyInstaller copied 2,214 `.py` files verbatim and never compiled
them. It is now imported, not copied.

**1b. The same trap, smaller, survived the fix.** `datas` still listed
`create_account.py`, `create_admin.py` and `wizard.py`, so those three shipped as
readable source in every customer copy long after `backend/app` stopped doing so.
`create_admin.py` was the one that mattered: it promotes any existing email to
admin and resets its password, and a licensed copy is meant to have **no
administrator at all** (§10). All three are already in `HIDDEN`, and `runner.py`
only ever *imports* them, so the bytecode in the archive was always enough.
`bundle/setup.py` does run them as files via `subprocess` — but that is the
**source** bundle, which carries its own copies and never uses this build.
**Check `datas` before every release: anything of ours listed there ships in the
clear.**

**2. Excluding `app` also hid its dependencies.** PyInstaller finds third-party
packages by following our imports. With `app` in `excludes`, it found none — the
build dropped from 372 MB to 118 MB, silently missing fastapi, uvicorn, pydantic,
cv2 and PIL. That fails on the customer's machine, not on ours. Hence
`NATIVE_DEPS`: with the app excluded, the runtime dependencies must be declared.
**If you add a new third-party import, add it to `NATIVE_DEPS`.**

**3. Both copies shipped at once.** Without the exclusion PyInstaller followed
`from app.main import app`, found the source on `pathex`, and bundled all 50
modules as bytecode *alongside* the compiled module — and the frozen importer
wins over `sys.path`, so the bytecode would have been what actually ran.

**4. `hiddenimports` names a package; it does not collect it.** PyInstaller
imports the name and then follows whatever that package's `__init__` imports.
`fastapi/__init__.py` never imports `fastapi.middleware.cors`, so the packaged app
died on its sixth line with `ModuleNotFoundError` — **after printing a completely
healthy banner**, which is exactly why it read as working for so long. With `app`
excluded there is nothing left to discover these by following, so `_FRAMEWORKS`
in `build_exe.py` is run through `collect_submodules()` in the spec.

`_FRAMEWORKS` deliberately omits numpy, cv2, PIL and transformers: those ship
PyInstaller hooks of their own that already collect correctly, and walking
transformers' submodule tree pulls in optional ML backends we exclude.
**If you add a third-party import, add it to `NATIVE_DEPS` — and if you import a
*submodule* of it, add the package to `_FRAMEWORKS` too.**

The warnings about `rapidocr…tensorrt`, `urllib3.contrib.emscripten` and
`onnxruntime.quantization` during the build are optional backends that are not
installed. They are expected; the build is fine.

### Where a customer's records live

A packaged copy asks on first run and remembers the answer in
`data-location.txt` beside the executable. Photos and scanned documents grow
without limit, and the folder someone unzipped into is usually Downloads on a full
C: drive; an external disk is a reasonable answer and moving afterwards is not.
`wizard.ask_location()` shows free space and refuses a read-only path before
anything is written.

**"Has this copy been used yet?" is `instance.env`, not `finmate.db`.** A licensed
bundle *ships* `data/finmate.db` already made — an empty database carrying the
branding — so the original test never fired on the one kind of copy it was written
for, and a real customer install went straight past the question.
`instance.env` holds the per-installation secrets and is generated on the first
real run, which makes it the marker that distinguishes "fresh out of the zip" from
"in use".

**Choosing another folder has to take the shipped data with it.** That folder is
not empty: it holds the signed licence, the vault key generated for that copy
alone, and the branded database. Left behind, the copy starts with no licence (so
it refuses to run) and a different vault key (so saved passwords are unreadable) —
both of which look like picking a folder broke the app.
`_carry_shipped_data()` copies rather than moves and never overwrites, and the
pointer is written only after it returns, so a failure leaves everything where it
was.

**A chosen location that is now unreachable is a hard stop, never a fresh start.**
Falling back to the default folder would create an empty database and present it as
the app, which to the owner is indistinguishable from every record they have ever
kept being deleted. `resolve_data_dir()` names the drive and the folder, says the
old records are untouched, and exits.

**Nothing before `prepare_environment()` may import `app`.** The secrets do not
exist yet, so `config.py` raises **SystemExit** — which `except Exception` does not
catch. The drive-not-connected message called `brand()`, and the config validation
error replaced it entirely; on the first-run path the same call would have killed
the launch outright. Hence `early_brand()`, which reads the executable's own name
and imports nothing. Verified on a real build: the message now appears verbatim,
correctly branded, and no database is created.

### Never ship without launching the executable

Every check in the table below is static inspection of the zip, and the crash
above passed all of them. The build printed its banner, created `finmate.db` with
a WAL, and *then* died — so "the database was created" was taken as proof it ran.
It was not. Before any release: build a licensed copy, seed an account, run the
`.exe`, and drive it over HTTP. Doing that the first time found the crash
immediately.

### What this does not do

It is not encryption, and nothing that runs on someone else's machine can be.

Docstrings and string literals survive as data in the binary — true of any native
binary; `strings` works on C too. `--python-flag=no_docstrings` would remove them
but produces a module that dies at import on Python 3.13 with
*"SystemError: bad argument to internal function"*, so it is not used.

What it does achieve: the licence gate is machine code. It cannot be opened in an
editor and deleted, which was the actual exposure.

### One machine, both platforms

**Compiling** must happen on the matching system — PyInstaller freezes the
interpreter it runs under, and nothing changes that. **Issuing** does not. The two
were conflated, so a Windows machine refused to produce a Mac copy even when Mac
binaries were available to it.

`dist-app/<platform>/<APP_DIR_NAME>` holds a build per platform, and
`compiled_dir(platform)` picks the right one. `dist-app/App` stays the host
platform's build so every existing script and path keeps working.

So: compile the Mac build once per release — on a Mac, or with
`.github/workflows/build-mac.yml` on GitHub's macOS runners, which needs **no
secrets** because the signing key signs licences and release manifests, not the
app. After that one laptop issues both, from one copy of the source, with the
licences and the signing key never leaving it.

### Carrying the Mac build back — `Get Mac Build.bat`

**The workflow starts itself whenever `VERSION` changes.** It used to run only on
a tag, and this repo has never had one, so a version bump built the Windows half
and silently left the Mac half on the previous release — visible nowhere until it
reached a customer.

Then double-click **`Get Mac Build.bat`** (or
`python packaging/fetch_mac_build.py`). It waits out a build still in progress,
because the natural moment to run it is straight after pushing the bump and taking
the newest *finished* run then would fetch the release before — the same mismatch,
arrived at by being helpful.

It **refuses** an archive whose version differs from `VERSION` (`--any-version`
overrides), and refuses one with **zero symlinks**: a flattened `Python.framework`
looks perfectly healthy here and dies on the customer's Mac with a message naming
none of this. The tar is never unpacked on the way through — Windows cannot
represent those links.

Needs `GITHUB_TOKEN` in `backend/.env` (fine-grained, one repository, Actions
read-only). Deliberately **not** a `config.py` setting: the app has no business
holding a credential for the publisher's source repository, and a setting it never
loads is one no endpoint can ever return. `.env` is in `bundler.SKIP_FILES`, so it
never travels — like the signing key beside it.

Once per *release*, not per customer: every Mac copy is built from that one file.

`ready_platforms()` is what the screen asks — which builds are *present*, not what
this machine can compile. It accepts the Mac **tarball** as well as an unpacked
folder, because that is what `build_licensed()` accepts; asking only
`compiled_available()` greyed out a button for a copy that would have built fine. Verified by standing in a Mac-shaped build and issuing
both from Windows: the Mac bundle carries no `.exe`, its executable is unsuffixed
and is the Mac binary; the Windows one is a `.exe` and is the Windows binary.

The executable's suffix is chosen by the **target** platform, not by `os.name`.
Building a Mac copy on Windows looked for `App.exe` and renamed it to
`SafeNest.exe`, inside a bundle whose launcher expects a Unix executable.

### macOS symlinks: the trap that reaches the customer, not you

`Python.framework` is built from symlinks — `Python` → `Versions/Current/Python`,
`Versions/Current` → `3.13` — and the **code signature seals that structure**.
Flatten them and macOS refuses to load the interpreter at all:

    code signature ... not valid for use in process:
    library load disallowed by system policy

Nothing on the build side notices. The folder looks right, zips, sends, and dies
on the customer's Mac with a message that names none of this. It happened: a
customer received four identical 6.7 MB Pythons where there should have been one
file and three links.

**`shutil.copytree` dereferences by default.** That is where they were lost —
in `build_exe.py`'s per-platform copy, *on the Mac*, before anything was packed.
`symlinks=True` is not optional there.

**A zip cannot carry them either**, nor can a Windows filesystem. So the Mac build
travels as a **tar.gz** made on the Mac, and `bundler._mac_from_tar()` copies
entries from it straight into the customer's archive — symlink members are
re-added as symlinks and never materialised on a filesystem that cannot represent
them. `build_licensed` refuses a Mac copy on Windows unless that tarball is
present, rather than producing another broken bundle.

**CI fails the build when the archive contains no symlinks.** A count of zero is
not a warning; it is the whole defect, and the only other place it shows up is a
customer's machine. A healthy archive has ~116 of them, and is ~45 MB smaller than
the flattened one because the duplicates are gone.

### A copy can only be compiled on the system it is for

PyInstaller freezes the interpreter it runs under. There is no cross-compiling and
no flag that changes it, so **a Mac executable must be compiled on a Mac**.

`build_licensed()` did not check this. Asking for a Mac copy on Windows copied
`dist-app/App` — the Windows build — into a Mac-named folder, renamed nothing
that mattered, and zipped it. The result looks completely right: correct folder
name, correct licence, correct branding, `README.txt`, `THIRD-PARTY-NOTICES.txt`.
Inside is `SafeNest.exe`, a Windows binary, and a Mac cannot start it. Found in a
real bundle already sitting on the Desktop, extracted and ready to send.

It now refuses, naming the machine the build has to happen on. The Licences screen
also disables the button and says so **before** the build, because a bundle that
fails this way gives no sign until it reaches the customer. `/api/system/export`
reports `compiled_platform` for that.

To ship for Mac: copy the project to a Mac, `python packaging/build_exe.py
--native` there, and issue the copy from that machine. The licence itself is
platform-independent — the same signed token works on either.

### Issuing a copy to a customer

```
1. python packaging/build_exe.py --native       (once per release, per OS)
2. Licences → issue a licence → build for it
3. Send them the .zip
```

Step 2 calls `bundler.build_licensed()`, **not** `bundler.build()`. The routing is
in `system.py`: any build with a `licence_token` takes the compiled path. The
source bundle is for moving your OWN installation between your OWN machines.

`build_licensed()` copies `dist-app/App`, renames the executable to the app's
current name, then writes the `data/` folder the packaged runner already knows how
to read: an empty database, the branding, their signed licence, and a vault key
generated for that copy alone. It always produces a zip — one file to send, and on
a Mac the zip is the only way the executable arrives with its executable bit
intact.

**It refuses rather than falling back.** With no compiled build present it raises
with the exact command to run. A silent fallback to the source bundle would be the
one failure nobody notices until the code is already in someone else's hands.

Verified on a real copy for `L-32968114` (153 MB zip):

| Check | Result |
|---|---|
| Readable `.py` of ours | **0** |
| Our modules as bytecode | **0** |
| Compiled `app.*.pyd` present | yes |
| Publisher signing key / vault key / `.env` | **absent** |
| Database | 31 tables, **only** the branding row |
| Their own licence + fresh vault key | present |
| Executable named | `SafeNest.exe` |
| `THIRD-PARTY-NOTICES.txt` | present, 57 packages |

And driven as a running program (15 checks, all passing): the SPA is served with
the branded `<title>`, login works, the account is `role=user`, the licence reads
`OK` with 28 days left, an expense round-trips, a vault item encrypts and reveals
with the copy's own carried key, an unauthenticated call is 401, and the customer
gets 403 on the admin API. Tampering with `licence.key` and restarting returns
**402** on reads *and* writes, with the message correctly branded.

The 2,168 readable `.py` files still in the zip are all `transformers` (2,157) and
`cv2` (11) — those packages ship their own source, which is theirs to ship. Count
by owning folder rather than in total, or the number looks alarming and means
nothing.

### Third-party notices

`packaging/notices.py` writes `THIRD-PARTY-NOTICES.txt` beside the executable at
build time, and `copytree` carries it into every customer bundle. It reads the
**build output**, not `requirements.txt`: requirements say what we asked for, the
build says what the customer actually received, transitive packages included.

Two things it has to work around, both of which silently lost real obligations:

- **PyInstaller only copies `.dist-info` when something reads it at runtime.** The
  heavyweights — onnxruntime, sqlalchemy, bcrypt, cv2, transformers — arrive as
  bare folders with no metadata, and they are exactly the Apache-2.0 ones whose
  notices must travel. `_from_environment()` looks them up in the build venv.
- **Some packages leave no folder at all** (requests is one; its source lives
  entirely in the archive). Hence `collect(extra=...)`, which `build_exe.py` feeds
  `NATIVE_DEPS`.

Testing the folder for an `__init__.py` looks like the obvious guard and is wrong —
PyInstaller moves the source into the archive and leaves only binaries, so those
folders look empty of Python. That check alone cost 35 of the 57 packages.

---

## 12. Things that are easy to get wrong

- **`/api/health` does not touch the database.** The app can report healthy while
  every login fails. When sign-in breaks, check MySQL on 3307 first.
- **Cloudflare rewrites `no-cache` to `max-age=14400` on `/sw.js`**, pinning
  devices to an old build for four hours. `swUrl.ts` carries `?v=N` — bump it when
  `public/sw.js` changes. The real fix is Cloudflare → Caching → Browser Cache TTL
  → "Respect Existing Headers".
- **The service worker is registered from one place** (`swUrl.ts`). Registering a
  bare `/sw.js` elsewhere creates a *second* worker fighting over one scope.
- **`navigator.serviceWorker.ready` never settles when nothing is registered.** It
  is a wait, not a check. `enable()` and `disable()` awaited it directly, so the
  Daily reminder switch could be pressed and simply sit there — no error, no
  toast, no movement, because the promise was pending and always would be. Both
  now go through `readyRegistration()`, which registers on demand and rejects
  after 10s.
- **Push needs a secure origin, and the LAN address is not one.** The app tells
  people to open `http://192.168.x.x:8080` on their phone; browsers withhold the
  push APIs there, and the old message blamed the *browser*. `blockedReason()`
  checks `window.isSecureContext` first and names the real cause.
- **`int(x or 30)` is a trap** — `0` is falsy, so a zero-day licence silently
  became 30 days. See `licences.py::_days()`.
- **CLIP cosine scores are not comparable across queries.** Nonsense text can
  outscore a real word. There is no usable threshold; content search is disabled
  below 50 photos (`CLIP_MIN_LIBRARY`).
- **OCR emits one line per text *box*, not per printed line.** `ocr.py` uses a
  look-ahead window instead of assuming layout.
- **A "failed" flag has to be *read*, not just set.** `ocr._get()` recorded
  `_failed = True` and never consulted it, so every document and every indexer
  pass rebuilt the engine, failed identically, and printed again — a customer's
  console scrolling past itself every two seconds. One attempt, one message, and
  `available()` goes false so nothing keeps queueing work that cannot run.
- **A client's dropdown must offer values the ENUM accepts.** Several columns are
  MySQL ENUMs — `todos.priority` is `(low,medium,high)`, `todos.status` is
  `(pending,done)`, `insurance.frequency` is `half_yearly` with an underscore,
  `credit_cards.status` is `(paid,unpaid)`. The phone app's field table was
  written from memory and got four of them wrong; each looked fine in the form
  and was refused by the database on save. `SELECT column_type FROM
  information_schema.columns WHERE data_type='enum'` before writing one, and note
  that SQLite does not enforce them — so a customer copy accepts what this
  installation rejects. `safenest-mobile/test/record_form_test.dart` pins them.
- **A field a router does not list is silently dropped, not refused.** Every
  record router filters the body through its own `FIELDS` (or `CONFIG[...]
  ["fields"]` in `resources.py`). Anything else is ignored without an error, so a
  form offering it looks like it saved and the value is simply gone. `cards.py`
  accepts neither `statement_amount` nor `status`, and the phone offered both.
  Nothing in the product writes `statement_amount` at all.
- **`paid_this_month` is what says a card or a loan is paid.** Not `is_paid`, not
  `paid` — neither is a field of anything, and a client reading them shows
  "unpaid" for ever however often the button is pressed. The state lives in
  `CardPayment` / `LoanPayment` rows keyed by period, not on the card itself.
- **Reminders carry a `due_time`, and it is a `VARCHAR(5)` holding "HH:MM".**
  Every other date here goes through `FlexDate` because MySQL and SQLite
  disagree; a short string agrees with both and compares to the current minute
  exactly. `scheduler.run_reminders()` rings it — today's only, `now >= target`
  so a PC asleep at 18:30 still fires once when it wakes, and `notified_on`
  stops it firing again every minute for the rest of the evening. Editing the
  time or reopening the reminder clears `notified_on`, or the alarm you just set
  stays silent. An unreadable time is refused by the router rather than stored:
  a reminder that sits in the list looking set and never arrives is worse than
  one that was never accepted.
- **`scheduler.start()` no longer requires push to be configured.** It used to
  return early without VAPID keys, so on those installations the whole thread
  never ran. `push.notify()` writes the in-app row before it tries any device, so
  a timed reminder reaches the bell either way; only the daily digest half is
  still gated.
- **Native DLLs in a subfolder need their directory registered.**
  `onnxruntime_pybind11_state.pyd` lives in `_internal/onnxruntime/capi/` and
  needs the Microsoft C++ runtime, which PyInstaller puts in `_internal/`. Since
  Python 3.8 an extension module's dependencies are no longer resolved via PATH,
  so an unregistered folder is simply not searched, and the whole of onnxruntime
  dies with *"DLL load failed … A dynamic link library (DLL) initialization
  routine failed"* — taking OCR, face matching and photo search with it. Two
  belts: `runner.py::_register_dll_dirs()` calls `os.add_dll_directory()` for
  both folders, and `build_exe.py::mirror_msvc_runtime()` copies the runtime
  beside onnxruntime's own DLLs after the build. Seen on a customer's Windows 10
  machine and never on the build machine, which is what a search-path difference
  looks like from outside.
- **Table names are not what you would guess, and three cost real time.**
  The activity table is **`audit_logs`**, not `activity_log`. Push tokens live
  in **`push_subscriptions`** (with a `kind` of `web` or `fcm`) — **`device_tokens`
  is something else entirely**, the Shortcuts upload credential that reaches one
  endpoint and nothing else. Reminders key on **`due_date`**, not `remind_date`.
  Read `information_schema` before writing a throwaway query; guessing costs a
  round trip each time.
- **`content_hash` is not the hash of what the phone sent.** It is taken after
  re-encoding and stripping metadata, deliberately, so a photo and its shared
  copy collide — which is what the duplicate finder wants and what a phone
  cannot reproduce without doing the same decode. **`source_hash` is the raw
  bytes**, and it exists so `POST /api/gallery/have` can answer "which of these
  do you already hold?" before a backup sends a library the server already has.
  Backfilled from the stored originals: exact for JPEG and video (the file on
  disk IS what was sent), never matching for HEIC (the stored file is a
  re-encode). That asymmetry is safe in one direction only — a wrong
  `source_hash` costs a needless upload and can never cause a photo to be
  SKIPPED, because a match means identical bytes.
- **`/api/gallery/have` must exclude trashed rows.** Reporting a photo in the
  bin as held would have the phone skip it for ever, and it would be gone from
  both places.
- **Text search matches a person's NAME, and the clustering names nobody.** It
  calls them "Person 3", "Person 12". So "search by person" was reachable only
  for faces somebody had already named — on this installation, none of the six.
  `?person=<id>` on the gallery index exists so a face can be tapped instead,
  and it composes with the date grouping and the other filters where
  `/api/people/{id}/photos` cannot.
- **The API runs as a SYSTEM scheduled task and cannot be restarted without
  elevation.** `schtasks /end /tn AppAPI` from an ordinary prompt answers
  "Access is denied". Double-click **`Restart App API.bat`**, which self-elevates.
  To test new code without that, start a second uvicorn on another port against
  the same database — and **kill any old one first**: a stale process on the
  test port answers `/api/health` perfectly while serving code from an hour ago,
  which reads exactly like a route that failed to register.
- **A migration entry keys on a COLUMN existing.** Adding an index that way —
  `("gallery_photos", "source_hash_idx", "ALTER TABLE ... ADD INDEX ...")` —
  re-runs on every single startup, because no column of that name ever appears.
- **`.btn.primary` does not exist in the CSS.** `.btn` is already the filled style;
  `.btn.ghost` is the quiet one.
- **`tk.Button` ignores `bg` on macOS** — the installer uses a drawn `Btn`
  (Frame + Label) because a purple button rendered as invisible white-on-grey.
- **Batch files**: nested `if` blocks break `%VAR%` expansion, and `%SystemRoot%`
  left unexpanded drops System32 from PATH. Use `goto` and `call set`.
- Times are **IST everywhere** via `ist.py`. Do not introduce `datetime.now()`.
- **`bundler.TEMPLATES` is the list of supported platforms**, and `system.py` reads
  it to answer `/api/system/export`. It replaced a constant called `PLATFORMS`;
  removing that without updating `system.py` and `make_bundle.py` produced a 500
  that the endpoint sweep caught. If you rename a module-level constant in
  `bundler.py`, grep the repo for it.
- **`dist-app/` and `build-app/` are stale packaged copies**, not source. Editing
  them does nothing. They are gitignored.

---

## 13. Verifying a change

```bash
# Frontend — tsc -b is stricter than --noEmit and catches things it misses
cd frontend && npm run build

# Backend
cd backend && venv\Scripts\python.exe -m compileall -q app
venv\Scripts\python.exe -c "import app.main"
```

There is no test suite in the repo. Verification is done with throwaway scripts
that drive the real running server. Useful patterns:

- **Endpoint sweep** — read `/openapi.json`, GET every path with an admin token,
  flag anything `>= 500`. Last run: 49 ok, **0 crashes**.
- **Licence gate** — start a *second* uvicorn on another port with its own SQLite
  database and only the public key, then drive it over HTTP through every state.
  A middleware test that mocks the middleware proves nothing.
- **Auth boundary** — every protected route must be 401 without a token;
  `/api/branding` is the only public one.

Mint an admin token for testing:
```python
from app.database import SessionLocal
from app.models import User
from app.security import create_token
s = SessionLocal(); admin = s.query(User).filter(User.role == "admin").first()
H = {"Authorization": f"Bearer {create_token(admin)}"}
```

**Always clean up test data.** Broadcasts, receipts and licences are real records
the operator sees.

---

## 14. Current state (11 August 2026)

- Branding: **SafeNest**, theme `#1656C6`, custom icon uploaded (`icon_version 1`)
- Users: `admin@finmate.app` (admin, 141 photos), `raghudarshan10@gmail.com`
  (user, **0 photos** — see below)
- Desktop **3.3**, built for BOTH platforms: Windows compiled here with
  `--native`, Mac fetched from CI. `dist-app/App` and `dist-app/mac/mac-app.tar.gz`
- Live licences:
  - `L-218E2470` Raghudarshan S — **perpetual**, seats 0 (unlimited)
  - `L-118D98BF` Ashok — expired 10 Aug. **The owner said on 10 Aug to ignore
    this one.** Do not act on it.
- Phone app **1.16.0** on TestFlight. Repo `D:\AI PRO\safenest-mobile`, which
  now has **its own CLAUDE.md** — read it before touching the phone.
- Public URL: **`safenest.raghudarshan.online`** via the named tunnel
  `b6ea7271-4d37-414e-9899-55be7f3903c5` (changed from `finmate.raghudarshan.online`
  on 15 Aug — DB `public_url`, `.env`, and the tunnel config all switched; the old
  hostname now returns 404 at the tunnel). **This machine's LAN address is now
  `192.168.31.159`** (it was `192.168.0.170`).
  **Changing the tunnel hostname:** edit the hostname in `cloudflared/config.yml`
  (the source of truth — the AppTunnel SYSTEM task reads it via `--config`, and the
  Web-address screen writes it) then double-click **`Restart App Tunnel.bat`** (it
  self-elevates). cloudflared keeps THREE config copies — the repo one, the user's
  `~/.cloudflared`, and (because the task runs as LocalSystem) the SYSTEM profile at
  `System32\config\systemprofile\.cloudflared`. If they disagree, a connector
  reading a stale one serves the old hostname; the restart helper syncs the repo
  config into the other two before restarting, and kills stray connectors (they
  accumulate and Cloudflare load-balances across all of them).
- **The `safenest` repo is PUBLIC** as of 8 Aug, so GitHub's free macOS minutes
  would build the Mac half. Anyone can build and run this from source with no
  licence; the Ed25519 signing key is not in the repo, so nobody can mint
  licences, but the gate only binds people who take the compiled build.

### Shipped 10–11 August

- **`source_hash` + `POST /api/gallery/have`** — a phone can ask what the server
  already holds before uploading. See §12. All 141 existing rows backfilled.
- **`?person=<id>`** on the gallery index, so a face can be tapped. Scoped to
  the caller's own people; another account's id is 404.
- Phone 1.16.0: the Videos filter (a reset was refused whenever a page was in
  flight, which pressing a chip mid-scroll always is), the pre-flight backup
  check, a strip of faces in the gallery, month grouping when zoomed out, and
  Screenshots / Recently added chips that the server always answered and only
  Collections ever asked for.
- Committed but **not yet tagged**: the backup headline now reports what was
  uploaded rather than the size of the library.

### Known outstanding

1. **iPhone push has never worked, and the cause is found.** There was no
   `aps-environment` entitlement in the iOS project at all, so iOS never issued
   an APNs token and Firebase never had one to wrap. `push_subscriptions` holds
   two rows, both `kind='web'` from Safari in July — not one from the phone app
   across three releases. The fix is on the branch **`push-entitlement`** and is
   deliberately NOT on main: signing is manual, and an entitlement the profile
   does not carry fails the build outright. Four owner-only steps unblock it —
   they are listed in the phone app's CLAUDE.md §7.
2. **Two-step sign-in has no screens.** `totp.py`, the login challenge and the
   setup endpoints are done and tested (22/22, plus the RFC 6238 vectors), and
   there is no way to turn it on from either client. **Deferred twice by the
   owner** — this is a decision, not an oversight.
3. `VAULT_KEY_LEGACY_HEX` — the migration now reports nothing remains on the
   legacy key, so the line can be deleted from `backend/.env`.
4. Cloudflare Browser Cache TTL is still "4 hours"; set it to "Respect Existing
   Headers".
5. `CF_ACCOUNT_ID` unset, so per-customer subdomains are built but disabled.
6. ~5,002 orphaned `photo_vectors` rows.
7. Desktop 3.3 has not been released to `L-218E2470`.
8. The phone app's minimum iOS is 13.0; Apple requires 15.0 from Spring 2027.

### One piece of history worth keeping, because it looked like a bug and was not

`raghudarshan10@gmail.com` has **0 photos**, and the phone reported a library of
1,048 needing backup. That is correct on both sides: on 8 Aug the owner emptied
the trash twice, deleting 79 then 42 photos permanently. Nothing was lost by the
app and there is no second server — the domain and the LAN address were both
checked reaching this installation.

The same account shows **43 `login_failed` and 0 `login_locked`**. That
distinction is the diagnosis: `login_locked` is recorded when the password was
*correct* but the account was locked, so every one of the 43 was a genuine
password mismatch. Five wrong tries locks for 15 minutes and `failed_logins`
persists between sessions, which is why a third attempt one evening tipped it
over rather than the fifth.

## 15. House style

Comments explain **why**, not what. The codebase is full of notes recording a real
failure and the reasoning that fixed it — that is the convention, keep it. If you
find yourself writing `# increment the counter`, delete it; if you are writing
`# 0 is falsy, so a zero-day licence became a thirty-day one`, keep it.

Match the surrounding code's naming and density. British spelling in user-facing
copy ("licence" the noun, "organised"). Amounts are rupees.
