# Re-shooting the storefront screenshots

Everything needed to regenerate `backend/storefront/img/*.webp` — the product
screenshots on the marketing site — without going anywhere near real data.

## The rule that makes this safe

Screenshots are taken from a **throwaway instance**: its own SQLite file, its own
port (8099), its own media root, and no licence signing key. The live MySQL on
**3307 is never touched**. Marketing images are not worth one invented row in the
owner's actual financial records, and the demo database is deleted afterwards.

## Order

```bat
rem 1. clean slate
rmdir /s /q %TEMP%\safenest-demo

rem 2. start the throwaway instance (leave this window open)
demo_env.bat

rem 3. in a second window, create the demo account
mkdemo.bat

rem 4. seed it
python seed_all.py          rem ledger: income, expenses, cards, loans,
                            rem insurance, investments, vault, reminders, todos
python seed_media2.py       rem gallery + documents, via paperwork.py

rem 5. set branding BEFORE capturing (see below), then
python capture_app.py       rem 2x PNGs into the scratchpad
python prep_images.py       rem resize -> WebP into backend/storefront/img
```

Then stop the instance and `rmdir /s /q %TEMP%\safenest-demo`.

## Four traps, each of which shipped a bad screenshot

**Wrong field names fail silently.** The resource endpoints drop unknown keys and
the columns have defaults, so a typo produces a 200 and a wrong-looking screen:

| Sent | Actually | Symptom on the shot |
| --- | --- | --- |
| `date` | `txn_date` | falls back to today — a month of spending stacked under "Today" |
| `invested` / `type` / `platform` | `invested_amount` / `invest_type` / `broker` | "invested Rs 0", "+0.0%", every holding typed "Other" |
| `type` | `policy_type` | every policy displays as "Other" |

**Empty modules photograph as empty states.** The vault shot was the vault's own
"No vault yet — 0 items" screen for months. Seed *every* module that appears in a
screenshot, including income (otherwise the expenses screen reads "Income Rs 0").

**The throwaway instance has no branding.** It defaults to app_name `App` on a
purple accent, so the sidebar says "App" and the whole UI is the wrong colour.
Set it to match production before capturing — `PUT /api/branding` with
`{"app_name":"SafeNest","short_name":"SafeNest","theme_color":"#1877F2"}`.

**Replacing an image in place does not reach visitors.** `/storefront-img/{name}`
sends `max-age=604800` on the reasoning that the filename changes when the picture
does. If you overwrite in place, Cloudflare serves the old one for a week. Either
rename, or bump the `?v=` on every reference in `index.html` **and** on the two
URL templates in `storefront.js` — and then bump `storefront.js?v=` itself, since
that file is edge-cached for four hours too.

## What goes in the pictures

`paperwork.py` renders the household paperwork the gallery and documents screens
are filled with — receipts, utility bills, warranty cards, policy schedules,
statements — with real type, either photographed on a surface or scanned flat.

Earlier versions used soft landscape gradients, which at thumbnail size are
coloured rectangles rather than photographs, and grey bars standing in for text,
which makes the documents screen read as a loading skeleton.

**Every company, account number and person in these images is invented.** An
earlier set used real companies; that has no place on a marketing page.
