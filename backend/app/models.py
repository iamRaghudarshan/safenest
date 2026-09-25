from datetime import date as _date, datetime as _datetime

from sqlalchemy import (
    DECIMAL, TIMESTAMP, Column, Date, DateTime, Float, ForeignKey, Integer, LargeBinary,
    Float, Index, String, Text, UniqueConstraint, text,
)
from sqlalchemy.types import TypeDecorator

from .database import Base


def _parse(value, want_date: bool):
    """Coerce an ISO string from a JSON body into a real date/datetime."""
    if not isinstance(value, str):
        # datetime -> date when the column only wants a day.
        if want_date and isinstance(value, _datetime):
            return value.date()
        return value
    text_value = value.strip()
    if not text_value:
        return None
    text_value = text_value.replace("T", " ").replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = _datetime.strptime(text_value[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
        return parsed.date() if want_date else parsed
    raise ValueError(f"{value!r} is not a valid date")


class FlexDate(TypeDecorator):
    """A Date column that also accepts 'YYYY-MM-DD' strings.

    MySQL parses date strings itself, so the routers have always been able to pass a
    value straight from a JSON body into the model. SQLite's driver refuses anything
    but a datetime.date, so the coercion lives here — one place, rather than every
    endpoint having to remember. Keeps the two backends behaving identically.
    """
    impl = Date
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return _parse(value, want_date=True)


class FlexDateTime(TypeDecorator):
    """As FlexDate, for columns that keep a time as well."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, _date) and not isinstance(value, _datetime):
            return _datetime(value.year, value.month, value.day)
        return _parse(value, want_date=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(120))
    email = Column(String(190), unique=True)
    password_hash = Column(String(255))
    role = Column(String(10), default="user")
    status = Column(String(12), default="active")
    phone = Column(String(20))
    avatar = Column(String(255))
    # --- two-factor ---
    # two_factor_enabled existed on its own for a long time, referenced by
    # nothing: a column that made the app look as though it had a second factor
    # while a password was the only thing between the internet and someone's
    # financial records. These are the parts that make it real.
    two_factor_enabled = Column(Integer, default=0)
    # The TOTP secret, encrypted at rest with the same vault key as saved
    # passwords. In the clear it is equivalent to the second factor itself —
    # anyone reading the database could generate valid codes for ever.
    totp_secret_enc = Column(Text)
    # SHA-256 of each unused recovery code, JSON. Hashed because they are
    # passwords that happen to be used once; kept because a licensed copy has
    # no administrator anywhere in it, so a lost phone would otherwise mean
    # losing every record permanently. See totp.py.
    recovery_codes = Column(Text)
    two_factor_at = Column(FlexDateTime)   # when it was turned on
    vault_recovery_hash = Column(String(255))
    # Bumped whenever every existing session must die (password change, admin reset).
    # Tokens carry the value they were minted with and are rejected once it moves on.
    token_version = Column(Integer, nullable=False, default=0, server_default=text("0"))
    failed_logins = Column(Integer, default=0)
    locked_until = Column(FlexDateTime)
    last_login_at = Column(FlexDateTime)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class UserModule(Base):
    __tablename__ = "user_modules"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    module_key = Column(String(40))
    can_view = Column(Integer, default=1)
    can_create = Column(Integer, default=1)
    can_edit = Column(Integer, default=1)
    can_delete = Column(Integer, default=1)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Loan(Base):
    __tablename__ = "loans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    lender = Column(String(120))
    loan_type = Column(String(60))
    principal = Column(DECIMAL(14, 2), default=0)
    interest_rate = Column(DECIMAL(5, 2), default=0)
    emi = Column(DECIMAL(14, 2), default=0)
    tenure_months = Column(Integer, default=0)
    outstanding = Column(DECIMAL(14, 2), default=0)
    start_date = Column(FlexDate)
    next_due_date = Column(FlexDate)
    status = Column(String(10), default="active")
    notes = Column(String(255))
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class LoanPayment(Base):
    """One row = a loan EMI marked paid for a given month (period 'YYYY-MM')."""
    __tablename__ = "loan_payments"
    id = Column(Integer, primary_key=True)
    loan_id = Column(Integer, index=True)
    user_id = Column(Integer, index=True)
    period = Column(String(7))  # 'YYYY-MM'
    amount = Column(DECIMAL(14, 2))
    paid_date = Column(FlexDate)
    created_at = Column(FlexDateTime)


class CreditCard(Base):
    __tablename__ = "credit_cards"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    bank = Column(String(120))
    last4 = Column(String(4))
    credit_limit = Column(DECIMAL(14, 2), default=0)
    billing_day = Column(Integer)
    due_date = Column(FlexDate)
    statement_amount = Column(DECIMAL(14, 2), default=0)
    status = Column(String(10), default="unpaid")
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class CardPayment(Base):
    """One row = a credit-card bill marked paid for a given month (period 'YYYY-MM')."""
    __tablename__ = "card_payments"
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, index=True)
    user_id = Column(Integer, index=True)
    period = Column(String(7))  # 'YYYY-MM'
    amount = Column(DECIMAL(14, 2))
    paid_date = Column(FlexDate)
    created_at = Column(FlexDateTime)


class Insurance(Base):
    __tablename__ = "insurance"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    policy_type = Column(String(60))
    provider = Column(String(120))
    policy_no = Column(String(80))
    premium = Column(DECIMAL(14, 2), default=0)
    sum_assured = Column(DECIMAL(14, 2), default=0)
    frequency = Column(String(20), default="yearly")
    renewal_date = Column(FlexDate)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Investment(Base):
    __tablename__ = "investments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    broker = Column(String(120))
    invest_type = Column(String(40))
    name = Column(String(160))
    invested_amount = Column(DECIMAL(14, 2), default=0)
    current_value = Column(DECIMAL(14, 2), default=0)
    units = Column(DECIMAL(16, 4))
    maturity_date = Column(FlexDate)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    kind = Column(String(10), default="expense")
    category = Column(String(60))
    amount = Column(DECIMAL(14, 2), default=0)
    method = Column(String(40))
    txn_date = Column(FlexDate)
    note = Column(String(255))
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Reminder(Base):
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    title = Column(String(160))
    module_ref = Column(String(40))
    due_date = Column(FlexDate)
    # "HH:MM", or NULL for a reminder that is simply due that day.
    #
    # A string rather than a TIME column, deliberately. Every other date here goes
    # through FlexDate because MySQL and SQLite disagree; a five-character string
    # agrees with both, survives JSON untouched in either direction, and compares
    # to the current minute exactly — no parsing, no timezone, no dialect. The one
    # thing it gives up is date arithmetic in SQL, and nothing here does any.
    due_time = Column(String(5))
    # The last day this reminder's own alarm fired. The scheduler wakes every
    # minute, so without it a reminder due at 18:30 would fire at 18:30, 18:31,
    # 18:32 … for the rest of the evening.
    notified_on = Column(FlexDate)
    recurrence = Column(String(10), default="none")
    is_done = Column(Integer, default=0)
    notify_push = Column(Integer, default=1)
    notify_email = Column(Integer, default=0)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Todo(Base):
    __tablename__ = "todos"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    title = Column(String(200))
    priority = Column(String(10), default="medium")
    due_date = Column(FlexDate)
    status = Column(String(10), default="pending")
    recurrence = Column(String(10), default="none")
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Note(Base):
    """A Google-Keep-style note: a free-text note OR a checklist, with a colour,
    labels, pin and archive. Checklist items live in NoteItem. Images and a
    per-note reminder are stage 2 (reminder_at is here already so the column need
    not be added later)."""
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    title = Column(String(255), default="")
    body = Column(Text, default="")            # the text of a 'note'
    kind = Column(String(16), default="note")  # 'note' | 'checklist'
    color = Column(String(20), default="default")
    labels = Column(Text, default="")          # JSON array of label strings
    pinned = Column(Integer, default=0)
    archived = Column(Integer, default=0)
    is_trashed = Column(Integer, default=0)
    reminder_at = Column(FlexDateTime)         # optional; wired to alarms in stage 2
    position = Column(Integer, default=0)      # manual order within its bucket
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class NoteItem(Base):
    """One line of a checklist note."""
    __tablename__ = "note_items"
    id = Column(Integer, primary_key=True)
    note_id = Column(Integer, index=True)
    user_id = Column(Integer, index=True)      # carried so a stray query can scope
    text = Column(String(1000), default="")
    checked = Column(Integer, default=0)
    position = Column(Integer, default=0)


class Habit(Base):
    __tablename__ = "habits"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    name = Column(String(120))
    # An emoji stored as text — the phone and the browser both render it directly,
    # and it needs no icon set shipped. A blank falls back to a default in the UI.
    icon = Column(String(16), default="")
    # One of the --c-* tints, stored as the CSS var name (e.g. "var(--c-habits)")
    # so both clients paint the same accent without a shared palette table.
    color = Column(String(40), default="var(--c-habits)")
    # "build" (do it) or "quit" (avoid it). Both track the same way — a quit habit
    # is a day you stayed clean — but the wording and streak framing differ.
    kind = Column(String(10), default="build")
    # How often the goal applies: "daily" (every day), "weekdays" (only the days in
    # `weekdays`), or "weekly" (any `weekly_target` days within a week). Strings, not
    # a SQL ENUM — the same dialect trap as todos.status (see routers/todos.py).
    goal_type = Column(String(10), default="daily")
    # For goal_type="weekdays": a CSV of ISO weekday numbers Mon=1…Sun=7, e.g.
    # "1,2,3,4,5" for weekdays only. Empty means every day.
    weekdays = Column(String(20), default="")
    # A day counts as done when its logged total reaches this. 1 is a plain tick;
    # >1 with a `unit` is a measured goal (8 glasses, 30 minutes).
    target_count = Column(Integer, default=1)
    # What target_count counts — "glasses", "min", "pages". Blank = a simple tick.
    unit = Column(String(24), default="")
    # For goal_type="weekly": how many days in the week must be done.
    weekly_target = Column(Integer, default=3)
    # "HH:MM" reminder, same rationale as Reminder.due_time — a 5-char string agrees
    # with both dialects and compares to the current minute exactly.
    reminder_time = Column(String(5))
    notified_on = Column(FlexDate)
    note = Column(Text)
    archived = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class HabitLog(Base):
    __tablename__ = "habit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    habit_id = Column(Integer)
    # The day this check-in belongs to (not when it was recorded). One row per
    # habit per day; `count` accumulates for measured goals.
    log_date = Column(FlexDate)
    count = Column(Integer, default=1)
    created_at = Column(FlexDateTime)


class GalleryPhoto(Base):
    __tablename__ = "gallery_photos"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    filename = Column(String(255))
    caption = Column(String(255))
    taken_at = Column(FlexDate)
    is_favorite = Column(Integer, default=0)
    # Out of the timeline, still in the library. Archive is NOT a soft delete:
    # the photo keeps its files, its faces, its search text and its albums, and
    # it is still found by search and still counted in storage. The only thing
    # it stops doing is appearing in the main grid — for the receipts, the
    # screenshots of a wifi password, the twelve shots of a whiteboard that are
    # worth keeping and not worth scrolling past every day.
    # The place name for lat/lon, worked out once and kept.
    #
    # Resolving coordinates to "Goa" costs a lookup, and doing it per request
    # means doing it thousands of times for an answer that cannot change. The
    # coordinates stay in lat/lon; this is only the label.
    place = Column(String(120), index=True)
    # The edit currently applied, as JSON, or NULL for an untouched photo.
    #
    # The EDIT is stored rather than only its result, so the picture can be
    # re-rendered from the pristine original at any time — which is what makes
    # "revert" free and what stops a second crop compounding the loss of the
    # first. It also means the screen can open the editor showing the sliders
    # where the person left them, instead of at zero over a photo that is
    # visibly not unedited.
    edit = Column(Text)
    is_archived = Column(Integer, default=0)
    is_trashed = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    content_hash = Column(String(64), index=True)  # sha256 of normalised JPEG — exact-dup key
    # sha256 of the bytes exactly as the DEVICE holds them.
    #
    # content_hash above is taken after re-encoding and stripping metadata, so
    # a photo and its shared copy match — which is what the duplicate finder
    # wants and what a phone cannot reproduce without doing the same decode.
    # This one a phone CAN compute by reading the file, which is the whole
    # point: it lets a backup ask "which of these do you already have?" before
    # sending a single byte, instead of uploading a library to find out.
    source_hash = Column(String(64), index=True)
    phash = Column(String(16), index=True)          # 64-bit dHash (hex) — perceptual/near-dup key
    # Text found in the picture: whiteboards, receipts, parcel labels, screenshots.
    # NULL = never read; "" = read and genuinely had no text.
    ocr_text = Column(Text)
    ocr_at = Column(FlexDateTime)
    # --- capture metadata, read from EXIF at upload (all optional) ---
    orig_name = Column(String(255))   # filename as it left the user's device
    width = Column(Integer)
    height = Column(Integer)
    camera = Column(String(120))      # "Apple iPhone 15 Pro"
    lens = Column(String(120))        # exposure summary: "26mm · f/1.8 · 1/120s · ISO 64"
    lat = Column(Float)               # decimal degrees, north positive
    lon = Column(Float)               # decimal degrees, east positive
    shot_at = Column(FlexDateTime)        # full capture timestamp when EXIF carries one
    # --- video ---
    # 'photo' or 'video'. Defaulted rather than nullable: every row that existed
    # before videos did IS a photo, and a NULL here would make every query that
    # filters on kind have to say "or null" for ever.
    kind = Column(String(8), default="photo")
    duration_ms = Column(Integer)     # videos only; NULL for a photo
    # When this went into the bin — the retention clock the purge reads.
    # is_trashed alone could say a photo was deleted but never for how long,
    # so the bin grew for ever and the disk with it. Documents have had this
    # column since July 2026; the gallery, which holds the big files, did not.
    trashed_at = Column(FlexDateTime)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)

    __table_args__ = (
        # The dedup in store_photo is a SELECT then an INSERT, and the upload
        # endpoint is deliberately threadpool-parallel, so two devices backing
        # up the same photo interleave between the two and both insert. This is
        # what makes that impossible rather than merely unlikely — the same
        # move uq_sync_user_uuid already makes for replayed records.
        #
        # content_hash is NULL until a row is hashed, and both MySQL and SQLite
        # allow repeated NULLs in a unique index, so unhashed and backfilling
        # rows are unaffected. source_hash is deliberately NOT part of the key:
        # store_photo rewrites it to self-heal the phone's pre-flight, and a
        # unique index on a column the code corrects in place would turn that
        # repair into a crash.
        UniqueConstraint("user_id", "content_hash", name="uq_gallery_user_content"),
        # The shape of every gallery query: this user's photos, not in the bin,
        # newest taken first. All three columns were unindexed, so each of those
        # was a full table scan — invisible at a few hundred photos and fatal at
        # the scale this product is for. Composite and in this order because the
        # filters come first and the sort last, which is the order the planner
        # can use in one pass.
        Index("ix_gallery_user_trash_taken", "user_id", "is_trashed", "taken_at"),
    )


class MasterList(Base):
    """A lookup LIST, as opposed to a value in one.

    The four original lists lived only as a dict in masters.py, so a person could
    add a bank but not a list of their own — no "Insurers", no "Landlords", no
    "Payment methods". The dict is now the seed for this table rather than the
    whole story, and `masters.py` validates against these rows.

    `type` stays the identity and is NEVER editable, including for a list
    somebody added themselves: the app's own code refers to `expense_category`
    and `document_category` by that string, and every Master row points at its
    list through it. The LABEL is what a person renames.

    `is_builtin` marks the four that the product itself reads. They can be
    renamed and their contents changed like any other, and they cannot be
    deleted, because a form that asks for `expense_category` would have nothing
    to ask.
    """
    __tablename__ = "master_lists"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    type = Column(String(40), index=True)
    label = Column(String(80))
    field = Column(String(10), default="emoji")   # 'emoji' or 'color'
    icon = Column(String(16))                     # emoji shown for the list itself
    is_builtin = Column(Integer, default=0)
    sort_order = Column(Integer, default=0)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Master(Base):
    """User-managed lookup lists ('masters') — e.g. document categories, banks,
    expense categories. One row per value, keyed by (user_id, type, key)."""
    __tablename__ = "masters"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    type = Column(String(40), index=True)   # document_category / bank / expense_category / vault_category
    key = Column(String(60))                # stable slug stored on records
    label = Column(String(80))
    emoji = Column(String(16))
    color = Column(String(20))
    sort_order = Column(Integer, default=0)
    is_active = Column(Integer, default=1)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class DocumentVersion(Base):
    """A previous copy of a document, kept when a new one replaces it.

    Section 35. Replacing a file used to overwrite the old one, so a wrong
    scan uploaded over a right one destroyed the right one — the single most
    expensive mistake a document store can allow, because the thing it was
    protecting is usually irreplaceable.

    The row carries its own filename: versions are separate files on disk, not
    deltas. Documents are small next to photographs and a delta chain is a way
    to lose all of them at once.
    """
    __tablename__ = "document_versions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    document_id = Column(Integer, index=True)
    #: 1 is the oldest kept copy. The CURRENT file is not a version row — it
    #: lives on the document itself, so reading a document never needs a join.
    version = Column(Integer)
    filename = Column(String(255))
    orig_name = Column(String(255))
    mime = Column(String(90))
    ext = Column(String(10))
    size_bytes = Column(Integer, default=0)
    content_hash = Column(String(64), index=True)
    note = Column(String(200))
    created_at = Column(FlexDateTime)

    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_docver_doc_version"),
        Index("ix_docver_user_doc", "user_id", "document_id"),
    )


class DocumentFolder(Base):
    """A folder in the documents tree.

    Adjacency list (each row names its parent) rather than a materialised path.
    A path column has to be rewritten for every descendant when a folder is
    renamed or moved, and the one thing people do constantly in a file tree is
    rename and move folders. Depth here is a handful, so walking parents to
    build a breadcrumb is a few cheap queries and never the slow part.

    parent_id NULL means the top level. There is no root row: a root that
    exists as a record is a root that can be renamed, moved into itself, or
    deleted.
    """
    __tablename__ = "document_folders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    parent_id = Column(Integer, index=True)
    name = Column(String(160))
    is_trashed = Column(Integer, default=0)
    trashed_at = Column(FlexDateTime)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)

    __table_args__ = (
        # Two folders of the same name in the same place is the thing every
        # file manager refuses, because afterwards neither the user nor a move
        # can tell them apart. Scoped per user and per parent, so two people —
        # and two different folders — may both hold a "Bank".
        UniqueConstraint("user_id", "parent_id", "name", name="uq_folder_user_parent_name"),
        Index("ix_folder_user_parent", "user_id", "parent_id", "is_trashed"),
    )


class Document(Base):
    """Secure document locker (ID cards, policies, certificates…). Files live in a
    PRIVATE dir (not the public /uploads mount) and are streamed only via an
    authenticated, ownership-checked endpoint."""
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    title = Column(String(160))
    category = Column(String(40))       # id / financial / medical / property / vehicle / education / insurance / other
    doc_number = Column(String(120))    # e.g. Aadhaar/PAN/passport no. — shown masked in UI
    issue_date = Column(FlexDate)
    expiry_date = Column(FlexDate)
    notes = Column(Text)
    filename = Column(String(255))      # stored (opaque) file name in the private dir
    orig_name = Column(String(255))     # original upload name
    mime = Column(String(90))
    ext = Column(String(10))            # pdf / jpg / png / …
    size_bytes = Column(Integer, default=0)
    # Text read out of the file by app/ocr.py. NULL means "not looked at yet";
    # empty string means "looked at, found nothing" — the two must stay distinct
    # or every blank page is re-read on every pass, forever.
    ocr_text = Column(Text)
    ocr_at = Column(FlexDateTime)
    has_thumb = Column(Integer, default=0)
    is_favorite = Column(Integer, default=0)
    pages = Column(Integer, default=1)       # >1 for multi-page scans
    # Which folder it sits in. NULL means the top level, which is also what
    # every document created before folders existed has — so the feature
    # arrives with every existing document already correctly placed,
    # rather than needing a migration that guesses.
    # What the classifier thinks this is (see doctype.py), and who decided.
    # kind_source distinguishes a SUGGESTION from a CORRECTION: once somebody
    # has said what a document is, no later indexing pass may overwrite it.
    # Without that column a re-index quietly undoes every correction and the
    # user has no idea why their filing keeps changing.
    kind = Column(String(24))
    kind_confidence = Column(Float)
    kind_source = Column(String(8))          # 'auto' | 'user'
    folder_id = Column(Integer, index=True)
    # sha256 of the stored bytes. Documents had no dedup at all: the same
    # PDF uploaded twice produced two files and two rows, every time.
    content_hash = Column(String(64), index=True)
    is_trashed = Column(Integer, default=0)  # recycle bin — restorable until purged
    trashed_at = Column(FlexDateTime)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class VaultItem(Base):
    __tablename__ = "vault_items"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    title = Column(String(160))
    username = Column(String(190))
    url = Column(String(255))
    password_enc = Column(Text)
    notes_enc = Column(Text)
    category = Column(String(40))
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class PushSubscription(Base):
    """One row per device that opted in. A user can have several (phone, tablet,
    desktop); each has its own endpoint and encryption keys."""
    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    endpoint = Column(String(500), unique=True)
    p256dh = Column(String(255))
    auth = Column(String(255))
    user_agent = Column(String(255))
    # 'web' for a browser subscription, 'fcm' for a phone app.
    #
    # ONE TABLE, not two. A device is a device: the fan-out, the per-user
    # lookup, the pruning of dead registrations and "how many devices does this
    # person have" are all identical, and only the last step — how the bytes
    # actually leave — differs. Two tables would have meant two of each of
    # those, and the one that drifted would be whichever is used less.
    #
    # An FCM registration token goes in `endpoint`; p256dh and auth stay NULL,
    # because a native push has no payload encryption of its own.
    kind = Column(String(8), default="web")
    created_at = Column(FlexDateTime)
    last_sent_at = Column(FlexDateTime)


class NotificationPref(Base):
    """Per-user delivery settings. Absent row = notifications off."""
    __tablename__ = "notification_prefs"
    user_id = Column(Integer, primary_key=True)
    enabled = Column(Integer, default=0)
    send_hour = Column(Integer, default=9)      # local hour, 0-23
    send_minute = Column(Integer, default=0)
    # Which sections go into the digest.
    include_bills = Column(Integer, default=1)      # card bills + loan EMIs
    include_reminders = Column(Integer, default=1)  # reminders + todos
    include_expiry = Column(Integer, default=1)     # policies + documents expiring
    last_sent_on = Column(FlexDate)                     # guards against double sends
    updated_at = Column(FlexDateTime)


class Notification(Base):
    """One entry in a user's in-app notification list.

    Written for everything the app would push. A web push is best-effort — the OS
    may hold it, the permission may have been revoked, the device may be off — and
    the server is never told. This table is the reliable copy: whatever happened to
    the push, the notification is still in the app when the user opens it.
    """
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    kind = Column(String(30), index=True)   # digest / export / system
    title = Column(String(160))
    body = Column(Text)
    url = Column(String(255))               # in-app route to open on tap
    is_read = Column(Integer, default=0, index=True)
    pushed = Column(Integer, default=0)     # whether a push was accepted for delivery
    created_at = Column(FlexDateTime)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    action = Column(String(80))
    entity = Column(String(60))
    entity_id = Column(Integer)
    ip = Column(String(45))
    user_agent = Column(String(255))
    meta = Column(Text)
    created_at = Column(FlexDateTime)


class Person(Base):
    __tablename__ = "people"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    name = Column(String(120))
    cover_id = Column(Integer)
    # Hidden from the People grid without losing the grouping. Google Photos
    # calls it hiding; the faces stay clustered and the photos are untouched,
    # it simply stops being offered. Deleting the person instead would throw
    # away the clustering work and the faces would regroup on the next pass.
    is_hidden = Column(Integer, default=0)
    # Exactly one person may be "me", which is what makes "photos of me" a
    # searchable thing. Not enforced by a constraint because the enforcement is
    # "setting a new one clears the old", which is a rule about intent rather
    # than a shape the database can express.
    is_me = Column(Integer, default=0)
    # Which face is shown as this person, once it has been worked out
    # properly. Remembered because choosing it means decoding several
    # twelve-megapixel originals and measuring each candidate for sharpness
    # and which way the head is turned - far too slow to redo on every load
    # of the People page. Null means "not chosen yet", and anything that
    # changes which faces belong to this person sets it back to null.
    portrait_face_id = Column(Integer, nullable=True)
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class PhotoPerson(Base):
    __tablename__ = "photo_people"
    id = Column(Integer, primary_key=True)
    photo_id = Column(Integer)
    person_id = Column(Integer)
    created_at = Column(FlexDateTime)


class PhotoFace(Base):
    """One detected face. A row with person_id and embedding NULL is a marker
    meaning "this photo was scanned and had no face in it" — without it every
    face-less photo would be re-scanned on every pass, forever."""
    __tablename__ = "photo_faces"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    photo_id = Column(Integer, index=True)
    person_id = Column(Integer, index=True)
    # float16 vector, not JSON: 128 floats as text is ~2 KB per face and has to be
    # parsed for every comparison; packed it is 256 bytes and loads straight into numpy.
    embedding = Column(LargeBinary)
    bbox = Column(String(60))
    score = Column(DECIMAL(4, 3))
    created_at = Column(FlexDateTime)


class PhotoLabel(Base):
    """What CLIP thinks is in a photo — "dog", "beach", "food".

    A row per (photo, label) rather than a list on the photo, so the category
    browser is an indexed lookup instead of a LIKE over a joined string, and so
    a label can carry its own score.

    Rebuilt from the photo's CLIP vector whenever the vocabulary changes, which
    is why nothing here is precious: it is a cache of a derivable thing.
    """
    __tablename__ = "photo_labels"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    photo_id = Column(Integer, index=True)
    label = Column(String(40), index=True)
    score = Column(Float)
    created_at = Column(FlexDateTime)

    __table_args__ = (
        UniqueConstraint("photo_id", "label", name="uq_label_photo_label"),
        Index("ix_label_user_label", "user_id", "label"),
    )


class PhotoVector(Base):
    """CLIP embedding for one photo, enabling search by what is IN the picture.

    Its own table rather than a column on gallery_photos: the grid query selects
    whole rows 150 at a time and has no use for a 1 KB blob, and re-indexing under
    a different model is then a truncate rather than a schema change.
    """
    __tablename__ = "photo_vectors"
    photo_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)          # search is always owner-scoped
    model = Column(String(40))                     # which encoder produced it
    vec = Column(LargeBinary)                      # float16, 512 dims
    created_at = Column(FlexDateTime)


class Suggestion(Base):
    """A proposal this account has said no to.

    Only the NOs are stored. Suggestions themselves are recomputed from the
    library every time they are asked for, so there is no queue to keep in
    step with reality — and the key is derived from the thing itself
    ("collage:2026-05-01") rather than handed out, or a dismissal would
    attach to a row that no longer exists and the same proposal would come
    back wearing a new number.
    """
    __tablename__ = "suggestions"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_suggestion_user_key"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    key = Column(String(120), index=True)
    created_at = Column(DateTime)


class Album(Base):
    """A user-made collection of photos. A photo can sit in any number of albums;
    deleting an album never touches the photos themselves."""
    __tablename__ = "albums"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    name = Column(String(120))
    cover_id = Column(Integer)
    # A SMART album is a saved query, not a list of photos.
    #
    # Stored as JSON rather than columns because the set of things you can
    # filter by will grow — person, label, year, month, kind today, place
    # tomorrow — and each new one would otherwise be another migration and
    # another nullable column that most rows never use.
    #
    # NULL means an ordinary album: a hand-picked list in album_photos. The two
    # kinds share a table because everything else about them is identical, and
    # every screen that lists albums would otherwise need to merge two queries.
    rule = Column(Text)  # gallery_photos.id used as the album tile
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class AlbumPhoto(Base):
    __tablename__ = "album_photos"
    id = Column(Integer, primary_key=True)
    album_id = Column(Integer, index=True)
    photo_id = Column(Integer, index=True)
    created_at = Column(FlexDateTime)


class AppHost(Base):
    """A computer the app has run on.

    The app is expected to move — a laptop is replaced, a bundle is carried to a
    Mac, a copy is started from an external drive. When something looks wrong the
    first question is always "which machine is actually serving this?", and from a
    phone there is no way to tell. One row per machine, so the table doubles as
    the history of where the app has lived.
    """
    __tablename__ = "app_hosts"
    id = Column(Integer, primary_key=True)
    fingerprint = Column(String(64), unique=True, index=True)
    hostname = Column(String(120))
    platform = Column(String(20))        # windows / mac / linux
    os_name = Column(String(160))        # human-readable, e.g. "macOS 15.2"
    local_ip = Column(String(45))        # IPv6-sized
    public_url = Column(String(255))
    app_version = Column(String(20))
    data_dir = Column(String(500))       # where this machine kept the database
    first_seen = Column(FlexDateTime)
    last_seen = Column(FlexDateTime, index=True)


class License(Base):
    """A licence this installation has issued to somebody else.

    Publisher-side only: the customer's copy never has this table, only the
    signed token itself. Kept so a licence can be looked up, extended, re-sent
    or withdrawn after it has left the building — the signature alone is not
    revocable, which is the whole reason the revocation check exists.
    """
    __tablename__ = "licenses"
    id = Column(Integer, primary_key=True)
    key_id = Column(String(16), unique=True, index=True)   # "L-3F2A9C1B"
    name = Column(String(120))
    email = Column(String(160), index=True)
    role = Column(String(20), default="user")
    issued_on = Column(FlexDate)
    # NULL means it never expires — sold outright rather than rented. Every read
    # of this column has to allow for that; licensing.evaluate() short-circuits on
    # the signed `perpetual` flag before any date arithmetic happens.
    expires_on = Column(FlexDate, index=True)
    # How many sign-ins the household may have: the licence holder plus family.
    # 0 is unlimited. NULL means a licence issued before this existed, which
    # licensing.seats_allowed() reads as 1 — what those copies can do today.
    seats = Column(Integer, default=1)
    revoked_at = Column(FlexDateTime)                      # null while live
    revoke_reason = Column(String(200))
    note = Column(String(200))
    token = Column(Text)                                   # re-issuable without re-signing
    bundle_at = Column(FlexDateTime)                       # when an app was last built for it
    # Hosting provisioned for this customer, when the publisher offers it. The
    # tunnel token is a live credential for one subdomain of the publisher's
    # domain, which is why withdrawal deletes the DNS record rather than trusting
    # the customer's copy to stop using it.
    hostname = Column(String(255), index=True)             # "meera.example.com"
    tunnel_id = Column(String(64))
    tunnel_token = Column(Text)

    # What the customer's copy reports when it checks in. Operational facts only —
    # which machine, which build, when it was last alive — so the publisher can
    # answer "are they on the new version?" and "is this copy still in use?".
    # Deliberately NOT what they do with it: the product's promise is that their
    # records never leave their computer, and telemetry that broke that promise
    # would be worse than useless, it would be a betrayal of the thing sold.
    last_seen_at = Column(FlexDateTime, index=True)
    last_ip = Column(String(45))
    last_platform = Column(String(20))      # windows / mac / linux
    last_os = Column(String(120))
    last_version = Column(String(20))
    last_hostname = Column(String(120))     # the computer's own name
    checkins = Column(Integer, default=0)

    # Activation lock (Option B): the licence binds to the first machine that
    # activates it. A hashed hardware fingerprint, set once at activation; a
    # different machine is refused. Cleared by "reset activation" to allow a move.
    machine_id = Column(String(64), index=True)
    activated_at = Column(FlexDateTime)
    # Suspension is reversible and separate from revocation: a customer who has
    # not paid this month is not the same as one whose licence is withdrawn.
    suspended_at = Column(FlexDateTime)
    suspend_reason = Column(String(200))
    created_by = Column(Integer, index=True)               # users.id of the issuing admin
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class Release(Base):
    """A version of the app the publisher has built and can offer to customers.

    Publisher-side only, like License. The customer's copy never writes this — it
    fetches the signed manifest for the current release and decides for itself
    whether to take it.

    The zip is kept on disk rather than in the database: it is several hundred
    megabytes, and a blob that size in SQLite would be read into memory to serve
    it. `manifest` is the Ed25519-signed statement of version, size and checksum,
    which is the only part a customer trusts.
    """
    __tablename__ = "releases"
    id = Column(Integer, primary_key=True)
    version = Column(String(32), index=True)
    notes = Column(Text)                      # what changed, shown to the customer
    filename = Column(String(255))
    path = Column(Text)                       # where the zip actually sits
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String(64))
    manifest = Column(Text)                   # signed; re-servable without re-signing
    platform = Column(String(16), default="windows")
    # Only one release is offered at a time. Kept as a flag rather than "newest
    # row wins" so a bad build can be withdrawn by promoting the previous one,
    # without deleting the record of it having existed.
    is_current = Column(Integer, default=0, index=True)
    published_at = Column(FlexDateTime)
    published_by = Column(Integer)
    created_at = Column(FlexDateTime)


class LicenceRequest(Base):
    """A prospective customer asking for a licence, from the public storefront.

    Publisher-side only, like License. Holds NO secrets — never the signed token,
    only the public key_id once a request is approved. Manual approval by design,
    so a spammer or a double-tap creates rows, not licences, and one open request
    per email is kept rather than a pile of duplicates.
    """
    __tablename__ = "licence_requests"
    id = Column(Integer, primary_key=True)
    name = Column(String(120))
    email = Column(String(160), index=True)
    message = Column(String(500))
    platform = Column(String(16))                          # windows / mac, optional
    status = Column(String(16), default="pending", index=True)  # pending/approved/rejected
    key_id = Column(String(16), index=True)               # set once approved
    reject_reason = Column(String(200))
    source_ip = Column(String(45))
    created_at = Column(FlexDateTime)
    handled_at = Column(FlexDateTime)
    handled_by = Column(Integer)                           # users.id of the approver


class MailSettings(Base):
    """SMTP configuration for emailing customers (licence keys, announcements).

    Publisher-side only. One row (id=1). The password is AES-encrypted with the
    vault key via crypto.py — never stored or returned in plaintext.
    """
    __tablename__ = "mail_settings"
    id = Column(Integer, primary_key=True)
    host = Column(String(255))
    port = Column(Integer, default=587)
    username = Column(String(255))
    password_enc = Column(Text)                            # AES-encrypted
    from_addr = Column(String(255))
    from_name = Column(String(120))
    security = Column(String(8), default="tls")            # tls / ssl / none
    enabled = Column(Integer, default=0)
    updated_at = Column(FlexDateTime)


class MailLog(Base):
    """Every email queued and its outcome — a DB-backed send queue and audit trail.

    Rows start 'queued'; a background worker sends them one at a time and records
    'sent' or 'failed' with the reason. Bulk sends never block the request.
    """
    __tablename__ = "mail_log"
    id = Column(Integer, primary_key=True)
    to_addr = Column(String(255), index=True)
    subject = Column(String(255))
    body = Column(Text)                                    # plain-text fallback
    html = Column(Text)                                    # optional branded HTML part
    kind = Column(String(20))                              # licence/broadcast/request/ticket/test
    status = Column(String(10), default="queued", index=True)  # queued/sent/failed
    error = Column(String(300))
    attempts = Column(Integer, default=0)
    created_at = Column(FlexDateTime)
    sent_at = Column(FlexDateTime)


class SiteStat(Base):
    """One row per day: how many times the public download/site page was opened."""
    __tablename__ = "site_stats"
    id = Column(Integer, primary_key=True)
    day = Column(String(10), unique=True, index=True)      # YYYY-MM-DD (IST)
    visits = Column(Integer, default=0)


class Ticket(Base):
    """A customer support ticket — raised in the app or from the website."""
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)                  # null for website/anon
    name = Column(String(120))
    email = Column(String(190), index=True)
    subject = Column(String(200))
    status = Column(String(12), default="open", index=True)   # open/pending/closed
    priority = Column(String(10), default="normal")           # low/normal/high
    licence_key = Column(String(16))
    source = Column(String(10), default="app")                # app / web
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime, index=True)


class TicketMessage(Base):
    """One message in a ticket thread, from the customer or the support admin."""
    __tablename__ = "ticket_messages"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, index=True)
    author = Column(String(10))                            # customer / admin
    author_name = Column(String(120))
    body = Column(Text)
    created_at = Column(FlexDateTime)


class Broadcast(Base):
    """A message the publisher sends to everyone running a copy of the app.

    Publisher-side only. Customer copies never write this table; they fetch the
    entries addressed to them and turn each one into a local notification, so a
    message survives the phone being off exactly like every other notification.

    The point is being able to say "there is a new version" to people whose
    machines you do not administer and cannot reach any other way.
    """
    __tablename__ = "broadcasts"
    id = Column(Integer, primary_key=True)
    title = Column(String(160))
    body = Column(Text)
    url = Column(String(255))                # where to get the update, if anywhere
    kind = Column(String(20), default="news")   # news / update / urgent
    app_version = Column(String(20))         # the build this announces, if any
    # all = local users AND licensed copies; local = just this installation;
    # licensed = only the copies out in the world.
    audience = Column(String(20), default="all", index=True)
    delivered_local = Column(Integer, default=0)
    created_by = Column(Integer, index=True)
    created_at = Column(FlexDateTime, index=True)
    # Set when this message is a resend of an earlier one, so the admin list can
    # show "sent again" rather than looking like the same thing was typed twice.
    resend_of = Column(Integer, index=True)


class Branding(Base):
    """What this copy of the app calls itself, and the icon it wears.

    One row, always id 1. Kept in the database rather than in a config file so an
    administrator can change it from inside the app, and so a licensed copy
    carries its own name through a rebuild without anyone editing source.

    The icon files live beside the other media rather than in here; only the
    version counter is stored, and it is bumped on every upload so browsers,
    home-screen shortcuts and the CDN all fetch the new image instead of showing
    the old one from cache.
    """
    __tablename__ = "branding"
    id = Column(Integer, primary_key=True)
    app_name = Column(String(60), default="App")
    short_name = Column(String(20), default="App")     # home-screen label
    tagline = Column(String(120), default="")
    theme_color = Column(String(20), default="#5b3df5")
    icon_version = Column(Integer, default=0)              # 0 = still the shipped icon
    updated_at = Column(FlexDateTime)
    updated_by = Column(Integer)


class Hosting(Base):
    """The web address this installation answers on.

    One row, always id 1. In the database rather than in .env so it can be changed
    from inside the app: buying a domain and pointing it here should not mean
    editing a config file and restarting a server.

    Read through `weburl.public_url(db)`, never directly — that helper falls back
    to the .env value so an installation that has never touched this screen keeps
    working exactly as before.
    """
    __tablename__ = "hosting"
    id = Column(Integer, primary_key=True)
    # "" means "not published" — the app is reachable on the local network only.
    public_url = Column(String(255), default="")
    # The Cloudflare tunnel that carries it, when there is one. The token is a
    # credential: it is never returned to the browser in full.
    tunnel_hostname = Column(String(255), default="")
    tunnel_id = Column(String(64), default="")
    tunnel_token = Column(Text)
    updated_at = Column(FlexDateTime)
    updated_by = Column(Integer)


class AutoImport(Base):
    """A folder the app watches, so photos arrive without anyone doing anything.

    WHY THIS EXISTS
    Everything else that gets photos in needs a person: the file picker needs a
    selection (and an iPhone stops closing it above a hundred), and the Shortcuts
    route needs five minutes of setting up on the phone. Asked for something
    simple that "backs up the gallery automatically", both are the wrong shape —
    they are things you have to keep doing, or keep having done.

    A watched folder is the one that is genuinely set-and-forget: choose it once,
    and anything that ever appears in it is imported. What puts photos there is
    then somebody else's problem in the good sense — iCloud for Windows dropping
    them in, Windows importing them when the phone is plugged in, or a copy from
    the phone's DCIM folder. All of those already exist and none of them are ours
    to build or to break.

    ONE FOLDER PER USER. Not a list: two people's photos going to one library is a
    mistake you cannot see happening and cannot easily undo.

    `seen_key` in the scanner is path+size+mtime, which is cheap. The real
    duplicate check is still the content hash in store_photo — this only decides
    whether a file is worth opening at all, so re-scanning a folder of 20,000
    photos costs a directory walk rather than 20,000 decodes.
    """
    __tablename__ = "auto_imports"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    folder = Column(String(500), default="")
    enabled = Column(Integer, default=0)
    # Counters are for the screen: "is this doing anything?" is the only question
    # people ask of a background job, and it must be answerable at a glance.
    imported = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    last_scan_at = Column(FlexDateTime)
    last_error = Column(String(300), default="")
    created_at = Column(FlexDateTime)
    updated_at = Column(FlexDateTime)


class DeviceToken(Base):
    """A credential that can send a photo in, and do nothing else at all.

    WHY THIS EXISTS
    A web page cannot read an iPhone's photo library — the file picker is the only
    door, and it stops closing somewhere above a hundred photos. The Shortcuts app
    has the access the browser does not, so a shortcut can take the whole library
    and post it here. But a shortcut has to carry a credential, and it lives in an
    automation the owner may share, export, or simply forget about.

    So it is not a session. A JWT would let whatever holds it read the vault, the
    documents and every record in the app; this reaches exactly one endpoint, which
    accepts nothing but an image. That is enforced by there being no other route
    that will look at one — not by a scope field someone has to remember to check.

    STORED AS A HASH, like a password. SHA-256 rather than bcrypt, deliberately:
    bcrypt is slow on purpose to make low-entropy passwords expensive to guess, and
    this is 32 random bytes, where guessing is hopeless anyway. A bulk backup sends
    thousands of photos, and a deliberately slow hash on every one of them would be
    a self-inflicted denial of service.

    The plaintext is shown once, at creation, and never again — there is nowhere it
    could be read back from, which is the point.
    """
    __tablename__ = "device_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    # What the owner calls it, so revoking the right one is possible later.
    name = Column(String(60))
    token_hash = Column(String(64), index=True)   # sha256 hex of the secret
    # First few characters, to tell two tokens apart in a list without holding one.
    prefix = Column(String(12))
    uploads = Column(Integer, default=0)
    created_at = Column(FlexDateTime)
    last_used_at = Column(FlexDateTime)
    # Revoking keeps the row: "this phone stopped working on Tuesday" is worth
    # being able to answer, and a deleted row answers nothing.
    revoked_at = Column(FlexDateTime)


class BroadcastReceipt(Base):
    """Proof that one licensed copy actually collected one message.

    Written when a copy pulls /api/licence/announcements — the only moment the
    publisher ever learns a message arrived, because delivery is pull-only. Without
    this row there is no way to tell "waiting for a machine that has never been
    switched on" apart from "delivered days ago", and those look identical in the
    admin list while meaning opposite things.

    Keyed by licence key_id rather than a foreign key to licenses.id so a receipt
    survives the licence row being rewritten, and because key_id is what the
    public endpoint is given.
    """
    __tablename__ = "broadcast_receipts"
    id = Column(Integer, primary_key=True)
    broadcast_id = Column(Integer, index=True)
    key_id = Column(String(16), index=True)
    collected_at = Column(FlexDateTime)
    __table_args__ = (
        UniqueConstraint("broadcast_id", "key_id", name="uq_receipt_broadcast_key"),
    )


class SyncOp(Base):
    """One offline operation the phone has already had accepted.

    THE FAILURE THIS EXISTS TO PREVENT. A phone that saves a record while the
    server is unreachable replays it later. If the network drops *after* the
    server committed the row but *before* the phone read the reply, the phone
    cannot tell "it never arrived" from "it arrived and I missed the answer" —
    and retrying is the only safe-looking option. Without a record of what has
    already been accepted, every such retry silently creates a second copy of a
    real record, which is worse than the outage that caused it.

    So the phone mints a uuid per operation and the server remembers it. A
    replay of a uuid already here returns the server_id it produced the first
    time, and writes nothing.

    ONE TABLE FOR EVERY MODULE, deliberately. The alternative — a client_uuid
    column on expenses, loans, cards, insurance, investments, reminders, todos,
    habits and notes — is nine migrations to keep in step, on both dialects,
    forever, and one of them WILL be forgotten (see the v7 note in the phone's
    own history: is_short reached history's table and not the other two, and
    every download then failed silently).

    Kept per user because a uuid is generated on a device and two households
    must never be able to collide, however unlikely a v4 collision is.
    """
    __tablename__ = "sync_ops"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True)
    client_uuid = Column(String(64), index=True)
    module = Column(String(40))
    #: What the operation produced, so a replay can answer with it rather than
    #: repeating the work. Null for a delete, which has nothing to point at.
    server_id = Column(Integer)
    op = Column(String(10))          # create | update | delete
    processed_at = Column(FlexDateTime)
    __table_args__ = (
        UniqueConstraint("user_id", "client_uuid", name="uq_sync_user_uuid"),
    )
