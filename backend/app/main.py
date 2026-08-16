import os
import re
import threading

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .config import BACKEND_DIR, settings
from .crypto import reencrypt_legacy_items
from .database import Base, engine
from .models import (Album, AlbumPhoto, AppHost, Branding, Broadcast, AutoImport, BroadcastReceipt, DeviceToken, Document, Hosting, License, LicenceRequest, MailLog, MailSettings, Release,
                     Master, MasterList, Notification, NotificationPref, PhotoVector, PushSubscription, SiteStat, Ticket, TicketMessage,
                     UserModule, User)
from .routers import (activity, admin, auth, branding, autoimports, dashboard, devices, documents, hosting, household, masters, briefing, cards, releases,
                      expenses, gallery, licences, loans, mail, notifications, people, reminders,
                      resources, search, mobile, storefront, support, system, todos, vault)
from . import autoimport, autostart, hosts, indexer, ist, licensing, mailer, scheduler, tunnelrun


def _sqlite_topup() -> int:
    """Add columns a customer's existing database has not got yet. Returns how many.

    WHY THIS EXISTS
    This used to be `create_all()` and nothing else, on the reasoning that a SQLite
    file is either brand new or was written by this same version. Shipping updates
    breaks that assumption outright: create_all() creates missing TABLES and never
    missing COLUMNS, so a customer who updated to a build with a new field kept the
    old table and every query touching it failed with "no such column". Their data
    was still there and the app could not read it, which is the worst shape a data
    problem can take.

    Derived from the models rather than a hand-kept list, because the list is what
    gets forgotten: anything added to models.py is carried into existing customer
    databases automatically, and the publisher's MySQL list stays as it is.

    Columns are added NULLable whatever the model says. SQLite cannot add a NOT
    NULL column without a constant default, and a nullable column that the app
    then populates is recoverable where a refused migration is not.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.schema import CreateColumn

    insp = sa_inspect(engine)
    added = 0
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue          # create_all() will build it whole
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                try:
                    kind = col.type.compile(engine.dialect)
                except Exception:
                    kind = "TEXT"
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {kind}'
                default = getattr(col, "server_default", None)
                if default is not None and getattr(default, "arg", None) is not None:
                    ddl += f" DEFAULT {default.arg}"
                elif col.default is not None and getattr(col.default, "is_scalar", False):
                    value = col.default.arg
                    ddl += f" DEFAULT {value!r}" if isinstance(value, str) else f" DEFAULT {value}"
                try:
                    conn.execute(text(ddl))
                    added += 1
                    print(f"[migrate] {table.name}.{col.name} added")
                except Exception as exc:
                    # One column that will not go on must not stop the rest, and
                    # must not stop the app: the customer's records are readable
                    # either way, and a refused start helps nobody.
                    print(f"[migrate] could not add {table.name}.{col.name}: {exc}")
    return added


def _backup_sqlite(reason: str) -> str:
    """Copy the database aside before touching its schema. Returns the path, or "".

    Cheap insurance on a machine nobody administers: if a top-up goes wrong there
    is no DBA to call, and the customer's whole financial history is in this one
    file. Kept beside the database so it is found by whoever goes looking.
    """
    import shutil
    src = settings.sqlite_path
    if not src.is_file():
        return ""
    stamp = ist.now().strftime("%Y%m%d-%H%M%S")
    dest = src.with_name(f"{src.stem}-before-{reason}-{stamp}{src.suffix}")
    try:
        shutil.copy2(src, dest)
        # WAL contents are not in the main file yet; without these the copy can be
        # missing the most recent writes.
        for extra in ("-wal", "-shm"):
            side = src.with_name(src.name + extra)
            if side.is_file():
                shutil.copy2(side, dest.with_name(dest.name + extra))
        print(f"[migrate] database backed up to {dest.name}")
        return str(dest)
    except OSError as exc:
        print(f"[migrate] could not back up the database: {exc}")
        return ""


def _sqlite_pending() -> bool:
    """Is there any schema change to make? Checked before backing anything up.

    Without this every single launch would copy the database, which on a library
    of years of records is both slow and a way to fill a disk.
    """
    from sqlalchemy import inspect as sa_inspect
    try:
        insp = sa_inspect(engine)
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                return True
            have = {c["name"] for c in insp.get_columns(table.name)}
            if any(c.name not in have for c in table.columns):
                return True
    except Exception:
        return False
    return False


def _migrate() -> None:
    """Idempotent schema top-ups for tables created before a column existed.

    The ALTER list below carries long-lived MySQL installs forward. SQLite — every
    customer copy — is handled generically by _sqlite_topup() from the models.
    """
    if settings.is_sqlite:
        # Ordered deliberately: back up before altering, create whole new tables,
        # then add columns missing from tables that already exist.
        needed = _sqlite_pending()
        if needed:
            _backup_sqlite("update")
        Base.metadata.create_all(bind=engine)
        if needed:
            _sqlite_topup()
        _seed_module_grants()
        return

    stmts = [
        # How many sign-ins a licensed household may have (added August 2026).
        # NULL on rows issued before this, which seats_allowed() reads as 1.
        ("licenses", "seats", "ALTER TABLE licenses ADD COLUMN seats INT NULL"),
        # Content-hash exact-dup key for gallery photos (added July 2026).
        ("gallery_photos", "content_hash",
         "ALTER TABLE gallery_photos ADD COLUMN content_hash VARCHAR(64) NULL"),
        # Perceptual-hash (dHash) near-dup key for gallery photos (added July 2026).
        ("gallery_photos", "phash",
         "ALTER TABLE gallery_photos ADD COLUMN phash VARCHAR(16) NULL"),
        # Session-invalidation counter: bumped on password change so old JWTs die.
        ("users", "token_version",
         "ALTER TABLE users ADD COLUMN token_version INT NOT NULL DEFAULT 0"),
        # Document recycle bin + multi-page scans.
        ("documents", "is_trashed",
         "ALTER TABLE documents ADD COLUMN is_trashed TINYINT NOT NULL DEFAULT 0"),
        ("documents", "trashed_at", "ALTER TABLE documents ADD COLUMN trashed_at DATETIME NULL"),
        ("documents", "pages", "ALTER TABLE documents ADD COLUMN pages INT NOT NULL DEFAULT 1"),
        # Photo capture metadata read from EXIF (added July 2026). All nullable —
        # older photos simply show "not recorded" for the fields they never had.
        ("gallery_photos", "orig_name",
         "ALTER TABLE gallery_photos ADD COLUMN orig_name VARCHAR(255) NULL"),
        ("gallery_photos", "width", "ALTER TABLE gallery_photos ADD COLUMN width INT NULL"),
        ("gallery_photos", "height", "ALTER TABLE gallery_photos ADD COLUMN height INT NULL"),
        ("gallery_photos", "camera",
         "ALTER TABLE gallery_photos ADD COLUMN camera VARCHAR(120) NULL"),
        ("gallery_photos", "lens", "ALTER TABLE gallery_photos ADD COLUMN lens VARCHAR(120) NULL"),
        ("gallery_photos", "lat", "ALTER TABLE gallery_photos ADD COLUMN lat DOUBLE NULL"),
        ("gallery_photos", "lon", "ALTER TABLE gallery_photos ADD COLUMN lon DOUBLE NULL"),
        ("gallery_photos", "shot_at",
         "ALTER TABLE gallery_photos ADD COLUMN shot_at DATETIME NULL"),
        # Hosting provisioned per licence (added July 2026).
        ("licenses", "hostname", "ALTER TABLE licenses ADD COLUMN hostname VARCHAR(255) NULL"),
        ("licenses", "tunnel_id", "ALTER TABLE licenses ADD COLUMN tunnel_id VARCHAR(64) NULL"),
        ("licenses", "tunnel_token", "ALTER TABLE licenses ADD COLUMN tunnel_token TEXT NULL"),
        # Text read out of documents and photos (added July 2026).
        ("documents", "ocr_text", "ALTER TABLE documents ADD COLUMN ocr_text TEXT NULL"),
        ("documents", "ocr_at", "ALTER TABLE documents ADD COLUMN ocr_at DATETIME NULL"),
        ("gallery_photos", "ocr_text", "ALTER TABLE gallery_photos ADD COLUMN ocr_text TEXT NULL"),
        ("gallery_photos", "ocr_at", "ALTER TABLE gallery_photos ADD COLUMN ocr_at DATETIME NULL"),
        # Links a resent message back to the original (added July 2026).
        ("broadcasts", "resend_of", "ALTER TABLE broadcasts ADD COLUMN resend_of INT NULL"),
        # A reminder can now name an hour, not just a day (added August 2026).
        # NULL on every existing row, which is exactly right: those were set
        # before there was a time to give, and they keep arriving with the daily
        # summary as they always did.
        ("reminders", "due_time", "ALTER TABLE reminders ADD COLUMN due_time VARCHAR(5) NULL"),
        ("reminders", "notified_on", "ALTER TABLE reminders ADD COLUMN notified_on DATE NULL"),
        # Videos in the gallery. DEFAULT 'photo' rather than NULL: every row
        # that existed before videos did is a photo, and a NULL would make
        # every kind= filter say "or null" for the rest of the app's life.
        ("gallery_photos", "kind",
         "ALTER TABLE gallery_photos ADD COLUMN kind VARCHAR(8) NOT NULL DEFAULT 'photo'"),
        ("gallery_photos", "duration_ms",
         "ALTER TABLE gallery_photos ADD COLUMN duration_ms INT NULL"),
        # Two-factor. two_factor_enabled was already on the model and used by
        # nothing; these are what make it mean something.
        ("users", "totp_secret_enc", "ALTER TABLE users ADD COLUMN totp_secret_enc TEXT NULL"),
        ("users", "recovery_codes", "ALTER TABLE users ADD COLUMN recovery_codes TEXT NULL"),
        ("users", "two_factor_at", "ALTER TABLE users ADD COLUMN two_factor_at DATETIME NULL"),
        # Phone push. 'web' for a browser subscription, 'fcm' for the app.
        ("push_subscriptions", "kind",
         "ALTER TABLE push_subscriptions ADD COLUMN kind VARCHAR(8) NOT NULL DEFAULT 'web'"),
        # Lets a phone ask what the server already has before uploading it.
        ("gallery_photos", "source_hash",
         "ALTER TABLE gallery_photos ADD COLUMN source_hash VARCHAR(64) NULL"),
    ]

    # Face embeddings moved from JSON text to a packed float16 blob (July 2026).
    # The old external face service never ran, so no row was ever written — there
    # is nothing to convert, only a column type to correct.
    with engine.begin() as conn:
        kind = conn.execute(text(
            "SELECT data_type FROM information_schema.columns WHERE table_schema = DATABASE() "
            "AND table_name = 'photo_faces' AND column_name = 'embedding'")).scalar()
        if kind and kind.lower() in ("text", "longtext", "mediumtext"):
            conn.execute(text("DELETE FROM photo_faces"))
            conn.execute(text("ALTER TABLE photo_faces MODIFY COLUMN embedding BLOB NULL"))
    with engine.begin() as conn:
        for table, column, ddl in stmts:
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ), {"t": table, "c": column}).scalar()
            if not exists:
                conn.execute(text(ddl))
        # Indexes for fast dedup lookups (ignore if they already exist).
        for idx, col in (("ix_gallery_photos_content_hash", "content_hash"),
                         ("ix_gallery_photos_phash", "phash")):
            try:
                conn.execute(text(f"CREATE INDEX {idx} ON gallery_photos ({col})"))
            except Exception:
                pass

    # New Documents module (added July 2026): create its table and grant it to every
    # existing non-admin user who doesn't have it yet (admins bypass RBAC).
    Document.__table__.create(bind=engine, checkfirst=True)
    Master.__table__.create(bind=engine, checkfirst=True)  # user-managed lookup lists
    # Lists people define themselves, beyond the four the product ships with
    # (added August 2026). Seeded from masters.py's dict on first read, so an
    # installation that never opens the screen behaves exactly as it always did.
    MasterList.__table__.create(bind=engine, checkfirst=True)
    # Push notifications (added July 2026).
    PushSubscription.__table__.create(bind=engine, checkfirst=True)
    NotificationPref.__table__.create(bind=engine, checkfirst=True)
    # In-app notification list (added July 2026).
    Notification.__table__.create(bind=engine, checkfirst=True)
    # CLIP photo embeddings for search-by-content (added July 2026).
    PhotoVector.__table__.create(bind=engine, checkfirst=True)
    # Photo albums (added July 2026).
    Album.__table__.create(bind=engine, checkfirst=True)
    AlbumPhoto.__table__.create(bind=engine, checkfirst=True)
    # Which computers the app has run on (added July 2026).
    AppHost.__table__.create(bind=engine, checkfirst=True)
    # Licences issued to other people (added July 2026, publisher side only).
    License.__table__.create(bind=engine, checkfirst=True)
    # Messages sent to licensed copies (added July 2026).
    Broadcast.__table__.create(bind=engine, checkfirst=True)
    # Versions published to customers (added August 2026).
    Release.__table__.create(bind=engine, checkfirst=True)
    # Storefront licence requests, publisher side only (added August 2026).
    LicenceRequest.__table__.create(bind=engine, checkfirst=True)
    # SMTP settings for emailing customers, publisher side only (added August 2026).
    MailSettings.__table__.create(bind=engine, checkfirst=True)
    # Email send queue/log, site-visit stats, and support tickets (added August 2026).
    MailLog.__table__.create(bind=engine, checkfirst=True)
    SiteStat.__table__.create(bind=engine, checkfirst=True)
    Ticket.__table__.create(bind=engine, checkfirst=True)
    TicketMessage.__table__.create(bind=engine, checkfirst=True)
    # Delivery receipts for those messages (added July 2026). Without them the admin
    # list cannot tell "never collected" from "collected days ago".
    BroadcastReceipt.__table__.create(bind=engine, checkfirst=True)
    # The app's own name and icon, editable by an admin (added July 2026).
    Branding.__table__.create(bind=engine, checkfirst=True)
    # The public web address, editable from inside the app (added July 2026).
    Hosting.__table__.create(bind=engine, checkfirst=True)
    # Phone upload credentials for the Shortcuts route (added August 2026).
    DeviceToken.__table__.create(bind=engine, checkfirst=True)
    # A folder the app watches for new photos (added August 2026).
    AutoImport.__table__.create(bind=engine, checkfirst=True)
    _seed_module_grants()


def _seed_module_grants() -> None:
    """Grant the Documents module to existing non-admin users and finish any vault
    key rotation. Engine-agnostic — both startup paths end here."""
    from .database import SessionLocal
    db = SessionLocal()
    try:
        have = {uid for (uid,) in db.query(UserModule.user_id)
                .filter(UserModule.module_key == "documents").all()}
        for (uid,) in db.query(User.id).filter(User.role != "admin").all():
            if uid not in have:
                db.add(UserModule(user_id=uid, module_key="documents",
                                  can_view=1, can_create=1, can_edit=1, can_delete=1))
        db.commit()

        # Move any vault secrets still under a rotated-out key onto the current one.
        moved = reencrypt_legacy_items(db)
        if moved:
            print(f"[migrate] re-encrypted {moved} vault secret(s) with the current key")
        elif settings.vault_key_legacy_hex:
            print("[migrate] no vault secrets remain on the legacy key — "
                  "you can now delete VAULT_KEY_LEGACY_HEX from .env")
    finally:
        db.close()


# Lets the licence poller stop promptly on shutdown instead of sleeping out
# its interval while the process tries to exit.
_stop_licence = threading.Event()

app = FastAPI(title="API", version="2.0")
# Titled at startup instead of here: the name lives in the database, which
# is not open yet at import time.


@app.exception_handler(IntegrityError)
async def _integrity_error(request: Request, exc: IntegrityError):
    """A database constraint reaching the client as a bare 500 tells the user
    nothing and looks like the app is broken. Translate the common cases into a
    readable 422 instead — and still log the original for diagnosis."""
    raw = str(getattr(exc, "orig", exc))
    print(f"[integrity] {request.method} {request.url.path}: {raw}")

    msg = "Some required information is missing. Please check the form and try again."
    m = re.search(r"Column '([^']+)' cannot be null", raw)
    if m:
        field = m.group(1).replace("_", " ")
        msg = f"{field.capitalize()} is required."
    elif "Duplicate entry" in raw:
        msg = "That value already exists."
    elif "Data too long" in raw:
        msg = "One of the values is too long."
    return JSONResponse(status_code=422, content={"detail": msg})


#: What branding._row() used to write when nothing had set a name. A row holding
#: exactly this was created BY an older build, not chosen by anybody.
_STOCK_BRANDING = "App"


def _adopt_build_branding() -> None:
    """Replace a placeholder name left by an older build with this build's own.

    A customer's login screen said "App" over the stock rupee mark for days. The
    row was written by branding._row(), which creates one the first moment
    anything asks what the app is called, and older builds wrote a hard-coded
    "App" there.

    Three attempts to repair it failed, and they failed the same way: each copied
    the row out of the database the bundle ships, so each depended on that bundle
    still being beside the app. It is not, after an in-app update — a release is
    one file for every customer and cannot carry anyone's shipped data — and the
    repair then had nothing to read and said nothing about it.

    This needs no files. The build knows its own name (branding._build_brand),
    so a row that is still the old placeholder is simply corrected, on every
    platform, after any update, however the copy got into that state.

    A name somebody set on purpose is left alone: it only replaces a row where
    every part is still the placeholder.
    """
    from .database import SessionLocal
    from .models import Branding
    from .routers import branding as b
    if not b.DEFAULT_NAME or b.DEFAULT_NAME == _STOCK_BRANDING:
        return                      # this build has no name of its own to offer
    db = SessionLocal()
    try:
        row = db.query(Branding).filter(Branding.id == 1).first()
        if row is None:
            return                  # _row() will create it from the build's brand
        stale = ((not row.app_name or row.app_name == _STOCK_BRANDING)
                 and not (row.tagline or "").strip()
                 and int(row.icon_version or 0) == 0)
        if not stale:
            return
        row.app_name = b.DEFAULT_NAME
        row.short_name = b.DEFAULT_SHORT
        row.tagline = b.DEFAULT_TAGLINE
        row.theme_color = b.DEFAULT_THEME
        row.updated_at = ist.now()
        db.commit()
        b.forget_name()
        print(f"[branding] this copy had no name of its own — using "
              f"{b.DEFAULT_NAME}")
    finally:
        db.close()


def _enforce_licence_role() -> None:
    """The signed licence decides the holder's role; the database may not disagree.

    A licensed copy is meant to have no administrator at all -- an administrator
    there can reach User management, the Licences screen and "Move everything to
    another computer", none of which belong to a customer.

    It went wrong for a real customer. `licence.key` travels read-only inside the
    .app and is copied out on first run, and in the build that customer installed
    that copy happened *after* the first-run account was created. So the account
    was made with no licence in sight, the launcher fell to its "this is somebody's
    own installation" branch, and it made them an admin. The current build copies
    the licence out first, but that does not help a copy already in someone's
    hands: records live in Application Support and survive the app being replaced,
    so the wrong role outlives every update.

    Hence a correction at startup rather than a fix in the installer alone. The
    licence is Ed25519-signed and carries `role`, so it -- not a row anyone could
    have edited -- is the authority on what its holder may do. Matched on the
    licence's own email, so it can only ever demote the person the licence names.
    """
    if not settings.licensed_mode:
        return
    payload = licensing.parse(licensing.read_token(settings.license_path),
                              settings.license_public_key_hex) or {}
    # An unreadable or unsigned licence says nothing about anybody's role. Acting
    # on one would let an edited file change what an account may do, which is the
    # opposite of the point.
    if payload.get("state") in (licensing.INVALID, licensing.MISSING):
        return
    # Whoever the licence names AND grants admin to, if anyone. Everybody else in
    # a licensed copy is a user -- not just the licence holder. The holder was the
    # one made an admin by the first-run bug, but an admin can create more admins,
    # and those accounts are exactly the ones a check aimed at the holder would
    # walk straight past.
    keep = ""
    if payload.get("role") == "admin":
        keep = (payload.get("email") or "").strip().lower()

    from .database import SessionLocal
    from .models import User
    db = SessionLocal()
    try:
        wrong = [u for u in db.query(User).filter(User.role == "admin").all()
                 if (u.email or "").strip().lower() != keep]
        for u in wrong:
            u.role = "user"
            # Retire their existing sessions too. Leaving a token alive that was
            # minted while the row said admin is how a demotion becomes cosmetic.
            u.token_version = int(u.token_version or 0) + 1
        if wrong:
            db.commit()
            print(f"[licence] this copy is licensed to be used, not administered "
                  f"— corrected {', '.join(u.email for u in wrong)}")
    finally:
        db.close()


@app.on_event("startup")
def _on_startup() -> None:
    try:
        _migrate()
    except Exception as e:  # never block boot on a migration hiccup
        print(f"[migrate] skipped: {e}")
    try:
        _adopt_build_branding()
    except Exception as e:
        print(f"[branding] name check skipped: {e}")
    try:
        _enforce_licence_role()
    except Exception as e:
        print(f"[licence] role check skipped: {e}")
    # After the migration, so the branding table is certain to exist. This is what
    # /docs and any generated client are titled.
    app.title = f"{_app_name()} API"
    # Note which computer this is. A move shows up here as a new row, which is the
    # only signal anyone gets that the app is being served from somewhere else.
    try:
        from .database import SessionLocal
        db = SessionLocal()
        try:
            host = hosts.record(db)
            print(f"[host] running on {host.hostname} ({host.os_name}) at {host.local_ip}")
        finally:
            db.close()
    except Exception as e:
        print(f"[host] could not record: {e}")
    # Ask the publisher whether this copy's licence still stands. On a thread and
    # never awaited: the answer is not worth delaying startup for, and a customer
    # whose internet is down must still get their app.
    if settings.licensed_mode:
        def _wait_seconds(state: dict) -> float:
            """How long before asking the publisher again.

            Driven by the expiry date, not the clock. A copy with ten months left
            has nothing to learn from asking every fifteen minutes, and a customer
            who notices their records machine contacting the supplier all day reads
            it as the app reporting on them — which is a fair reading, and not what
            this is for.

            So: once at startup, then quiet until the licence is close to running
            out, then attentively while it matters. Expiry itself needs no network
            at all — the date is inside the signed licence and is checked offline
            on every request. This trip exists only to hear about a withdrawal.
            """
            days = state.get("days_left")
            if not isinstance(days, int):
                return settings.licence_poll_minutes * 60
            if days <= settings.licence_watch_days:
                return settings.licence_poll_minutes * 60
            # Sleep until the watch window opens, waking a little early so a
            # renewal that lands during it is still noticed.
            return max(3600.0, (days - settings.licence_watch_days) * 86400.0)

        def _check_licence():
            # Still a loop, not a single check at startup: a withdrawal has to be
            # able to land on a machine that is left running for weeks, which was a
            # real failure once -- a suspended copy kept working indefinitely
            # because the licence was only read at boot. What changed is the
            # cadence: see _wait_seconds().
            first = True
            delay = settings.licence_poll_minutes * 60
            while True:
                if not first:
                    _stop_licence.wait(delay)
                    if _stop_licence.is_set():
                        return
                first = False
                try:
                    s = licensing.refresh(settings.license_path,
                                          settings.license_public_key_hex)
                    delay = _wait_seconds(s)
                    when = (f", next check in {delay / 86400:.0f} days"
                            if delay >= 86400 else "")
                    print(f"[licence] {s.get('state')} — "
                          f"{s.get('reason') or 'valid'}{when}")
                    # Same trip, same thread: anything the supplier has announced
                    # since last time becomes a notification here.
                    db = SessionLocal()
                    try:
                        got = licensing.pull_announcements(
                            db, settings.license_path, settings.license_public_key_hex)
                        if got:
                            print(f"[licence] {got} announcement(s) received")
                    finally:
                        db.close()
                except Exception as e:
                    # Offline, or the supplier is down. Neither is the customer's
                    # fault and neither should stop the app — try again next tick.
                    print(f"[licence] check skipped: {e}")
        threading.Thread(target=_check_licence, daemon=True).start()

    scheduler.start()
    # The email queue worker — sends queued customer mail one at a time in the
    # background, so a bulk broadcast never blocks the request that started it.
    try:
        mailer.start_worker()
    except Exception as exc:
        print(f"[startup] mail worker: {exc}")
    # Photo indexing resumes itself: anything uploaded while the app was down, or
    # left over from an interrupted pass, gets picked up without anyone asking.
    try:
        indexer.start_if_idle_work()
    except Exception as e:
        print(f"[indexer] could not start: {e}")

    # The watched folder, if anyone has set one. Started unconditionally: the
    # thread checks for enabled rows itself, and a backup that only resumes when
    # somebody opens the settings screen is not a backup.
    try:
        autoimport.start()
    except Exception as e:
        print(f"[autoimport] could not start: {e}")

    # Keep the tunnel up alongside the app. The promise is that the owner's
    # records are reachable while their computer is on, and that needs the
    # connector alive too — not just this process. Inside the startup hook, not
    # at import: importing this module must not launch a subprocess, or every
    # tool that merely inspects the app would start a tunnel.
    tunnelrun.start()

    # A licensed copy IS somebody's personal server, so it starts with their
    # computer from day one rather than waiting to be told. Done once: if they
    # turn it off, it stays off.
    if settings.licensed_mode:
        try:
            data_dir = (settings.sqlite_path.parent if settings.is_sqlite
                        else BACKEND_DIR / "data")
            if autostart.ensure_default(data_dir):
                print("[autostart] enabled for this licensed copy")
        except Exception as e:
            print(f"[autostart] skipped: {e}")


@app.on_event("shutdown")
def _on_shutdown() -> None:
    _stop_licence.set()
    tunnelrun.stop()


# Content-Security-Policy for the built SPA. 'unsafe-inline' is needed for styles
# only (React writes inline style attributes); scripts stay strictly same-origin.
# blob: covers the object URLs used to render private documents.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    # media-src, added when the gallery learned to hold video. Without it a
    # <video> falls back to default-src, which happens to allow 'self' — so it
    # worked, by accident, and would have broken the day default-src tightened.
    "media-src 'self' blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    # Named rather than left to default-src, so each says what it means and a
    # future change to the default cannot quietly widen one of them.
    "frame-src 'none'",
    "worker-src 'self'",          # the service worker, and nothing else
    "manifest-src 'self'",
    # Anything that slipped through as http:// is fetched over https instead of
    # being blocked outright. On a customer's own domain this is the difference
    # between a mixed-content failure they cannot diagnose and it simply working.
    "upgrade-insecure-requests",
])


# Endpoints a licensed copy must answer even when its licence has lapsed. Without
# these the customer cannot sign in to read *why* it stopped, cannot see the
# expiry date to quote when renewing, and cannot install the replacement licence —
# a dead end that generates a support call for every renewal.
_LICENCE_OPEN = ("/api/health", "/api/auth/login", "/api/auth/me",
                 "/api/licence/status", "/api/licence/activate",
                 # The lapsed-licence screen still has to say the app's own name
                 # and show its icon, so reading the branding stays open. It is a
                 # read of the name and colour only — never anything about data.
                 "/api/branding",
                 # The sign-in screen's connection switch (LAN vs the public
                 # domain) is on that same lapsed-licence screen, so reading the
                 # addresses stays open too. Addresses only — never any data.
                 "/api/hosting/addresses")

# Getting your own records out is never blocked, in any licence state.
#
# A lapsed licence ends someone's right to *use* the software. It does not make
# their own records ours to withhold, and holding them hostage over a renewal is
# not a position to be in — commercially or otherwise. This used to be a
# grace-period-only exemption, which meant that three days after expiry a customer
# who simply forgot to renew could no longer get their data out at all.
#
# Covers the whole export flow by prefix: POST to start, GET to poll, and the
# history list. Still behind authentication like everything else — this lets the
# signed-in owner take their own data, not a stranger.
_DATA_OUT = ("/api/system/export",)


def _app_name() -> str:
    """The app's own name, for messages shown to a customer.

    Read straight from the database rather than cached: this runs only on the
    blocked-licence path, which is rare, and a stale name on the one screen that
    tells someone their copy has stopped would be a poor place to save a query.
    Falls back to the default if anything at all goes wrong — a licence message
    must never itself fail.
    """
    try:
        from .database import SessionLocal
        from .routers.branding import current as _branding
        db = SessionLocal()
        try:
            return _branding(db)["app_name"]
        finally:
            db.close()
    except Exception:
        from .routers.branding import DEFAULT_NAME
        return DEFAULT_NAME


@app.middleware("http")
async def licence_gate(request: Request, call_next):
    """Stop a licensed copy that has expired, been withdrawn, or lost its licence.

    Only ever active when licensed_mode is on, which the bundler sets for copies
    handed to other people. The publisher's own installation never has it, so this
    is inert here.
    """
    if not settings.licensed_mode:
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") or path.startswith(_LICENCE_OPEN):
        return await call_next(request)
    # Above the blocking check on purpose: expired, withdrawn, corrupt and missing
    # all reach it, because in every one of those cases the records are still the
    # customer's.
    if path.startswith(_DATA_OUT):
        return await call_next(request)

    state = licensing.cached_status(settings.license_path,
                                    settings.license_public_key_hex)
    kind = state.get("state", licensing.INVALID)
    if licensing.is_blocked(kind):
        return JSONResponse(status_code=402, content={
            "detail": state.get("reason") or f"This copy of {_app_name()} needs a valid licence.",
            "licence": {"state": kind, "expires_on": state.get("expires_on"),
                        "key_id": state.get("kid")},
        })
    # Export already returned above, so it needs no second exemption here.
    if kind == licensing.GRACE and request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse(status_code=402, content={
            "detail": state.get("reason") or "This licence has expired.",
            "licence": {"state": kind, "expires_on": state.get("expires_on"),
                        "key_id": state.get("kid"), "readOnly": True},
        })
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    # Every capability this app never uses, refused up front. A browser will not
    # even prompt, so a page that somehow ran here could not ask for the
    # microphone, the location, or a payment sheet.
    resp.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
        "magnetometer=(), gyroscope=(), accelerometer=(), midi=(), "
        "serial=(), bluetooth=(), display-capture=(), "
        "interest-cohort=(), browsing-topics=()")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    # Nobody else's page may fetch a photo, a document or an API response from
    # here, even with a URL. The signed media links are already owner-bound and
    # expiring; this stops a leaked one being embedded elsewhere at all.
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    # The Flash-era crossdomain.xml family. Long dead, still checked by scanners,
    # and free to close.
    resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    # Naming the server and its version tells a scanner which exploits to try
    # first. It buys an attacker time and buys us nothing.
    # _cached: a database round-trip per response for a header would be absurd.
    from .routers.branding import app_name_cached
    resp.headers["Server"] = app_name_cached()
    # --- caching policy -----------------------------------------------------
    # Cloudflare caches by file extension unless the origin says otherwise, which
    # previously froze /sw.js at the edge for 4h and pinned phones to an old build.
    # Set these explicitly rather than relying on defaults.
    path = request.url.path
    if path.startswith("/api/"):
        # Personal data: never let a shared proxy or browser hold on to it.
        resp.headers["Cache-Control"] = "no-store"
    elif path.startswith("/assets/"):
        # Vite content-hashes these, so a new build is a new URL — cache hard.
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest"):
        # Must be revalidated every load or a deploy can never reach the client.
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    # HSTS only over TLS — behind cloudflared/Tailscale the local hop is plain HTTP,
    # so trust the forwarded scheme they set.
    https = (request.url.scheme == "https"
             or request.headers.get("x-forwarded-proto", "").lower() == "https")
    if https:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, dashboard, briefing, loans, cards, resources, expenses, reminders, todos, vault, gallery, people, documents, masters, notifications, system, activity, admin, licences, search, branding, hosting, household, releases, devices, autoimports):
    app.include_router(r.router)
app.include_router(licences.public)   # /api/licence/... — customer-facing, separate prefix
app.include_router(releases.public)   # /api/licence/update, /download
app.include_router(household.updater) # /api/update — the customer half
app.include_router(mobile.router)     # /api/mobile/... — the phone's own updates
# The storefront exists ONLY on the publisher — the box that holds the signing
# key and issues licences. Registering it behind is_publisher means a customer
# copy never even has these routes, on top of the admin+publisher gates inside.
if settings.is_publisher:
    app.include_router(storefront.public)   # /api/public/download, licence-request
    app.include_router(storefront.admin_r)  # /api/licence-requests — admin review
    app.include_router(storefront.pages)    # /get — the download page itself
    app.include_router(mail.router)         # /api/admin/mail — SMTP settings
    app.include_router(support.admin_t)     # /api/admin/tickets — manage support
    app.include_router(support.public_t)    # /api/public/support — website tickets
# Icon files and the manifest. Registered here, ahead of the SPA mount below, so
# the generated manifest wins over the one compiled into the build — otherwise a
# renamed app would keep its old name on every home screen.
app.include_router(branding.files)

# NOTE: uploaded photos are deliberately NOT exposed through a static mount. They
# are served by /api/gallery/media/{name} against a short-lived, owner-bound
# signature — see app/signing.py. Re-adding a StaticFiles mount here would make
# every photo world-readable to anyone who learns (or guesses) a filename.


@app.get("/api/health")
def health():
    return {"ok": True, "service": "finmate-api", "version": "2.0"}


# Serve the built SPA (frontend/dist) from this same origin when it exists — used
# by the HTTPS "phone" server so the API and app share one secure origin. Mounted
# LAST so it never shadows /api. Absent in pure dev (Vite serves it).
# FRONTEND_DIST wins when set. Inside a packaged build the module path is not the
# source tree — app/ is frozen into the executable — so walking up from __file__
# lands somewhere that does not exist, and the app serves its API perfectly while
# returning 404 for the actual web page. The launcher sets this explicitly.
_dist = os.environ.get("FRONTEND_DIST") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")


def _branded_index() -> str:
    """index.html with the app's real name substituted into the head.

    The compiled index.html carries the build-time name in <title> and in
    apple-mobile-web-app-title, and those are read before any JavaScript runs. So
    a renamed app flashed the OLD name in the browser tab on every cold load, and
    an iPhone home-screen shortcut kept it permanently. branding.ts fixes the tab
    a moment later, which is exactly long enough to be seen.

    Same tactic as the generated manifest: registered ahead of the SPA mount so it
    wins over the file on disk.
    """
    import re
    from html import escape
    from .database import SessionLocal

    with open(os.path.join(_dist, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    try:
        db = SessionLocal()
        try:
            b = branding.current(db)
        finally:
            db.close()
    except Exception:
        return html      # a missing name must never cost the person their app

    name = escape(b["app_name"], quote=True)
    html = re.sub(r"<title>.*?</title>", f"<title>{name}</title>", html, count=1,
                  flags=re.S)
    html = re.sub(r'(<meta name="apple-mobile-web-app-title" content=")[^"]*(")',
                  lambda m: m.group(1) + name + m.group(2), html, count=1)
    html = re.sub(r'(<meta name="theme-color" content=")[^"]*(")',
                  lambda m: m.group(1) + escape(b["theme_color"], quote=True)
                  + m.group(2), html, count=1)
    return html


if os.path.isdir(_dist):
    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def _index():
        return HTMLResponse(_branded_index())

    app.mount("/", StaticFiles(directory=_dist, html=True), name="spa")
else:
    print(f"[spa] no built web app at {_dist} — API only")
