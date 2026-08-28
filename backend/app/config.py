"""Application settings.

Every secret is REQUIRED and loaded from `backend/.env` — there are deliberately
no in-code defaults, so the app fails loudly at boot rather than silently running
on a placeholder key that anyone reading the source would know.
"""
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Values shipped in earlier versions of this file. Refused outright so an old
# .env copied forward can't quietly reintroduce a known-public key.
_BURNED = {
    "change-me-finmate-react-secret-key-2026",
    "8f2b4c1d9e6a3f7b0c5d8e2a1f4b6c9d0e3a7f2b5c8d1e4a7b0c3d6e9f2a5b8c",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # --- database ---
    # "mysql" is the original server setup; "sqlite" keeps everything in one file so
    # the app can be copied to another machine (Windows or Mac) and just run.
    db_engine: str = "mysql"
    db_host: str = "127.0.0.1"
    db_port: int = 3307
    db_name: str = "finmate"
    db_user: str = "finmate"
    db_password: str = ""                  # required for MySQL, unused for SQLite
    # Where the SQLite file lives. Relative paths resolve against backend/.
    db_file: str = "data/finmate.db"

    # Root of the uploaded photos/documents tree. Relative to backend/ when not
    # absolute — the bundle points this at its own data folder.
    media_root: str = ""

    # --- session tokens ---
    jwt_secret: str                        # required
    jwt_algorithm: str = "HS256"
    # A YEAR, not 8 hours. A phone is a personal device the owner expects to stay
    # signed in on — an 8-hour token meant re-typing the password almost every time
    # the app was opened. Safe to make long-lived: users.token_version is bumped on
    # any password change, which kills every existing token at once, and the token
    # lives in the Keychain/Keystore, not anywhere world-readable.
    jwt_expire_minutes: int = 60 * 24 * 365   # 1 year

    # --- vault encryption (AES-256-GCM) ---
    vault_key_hex: str                     # required, 32 bytes hex
    vault_key_legacy_hex: str = ""         # previous key, only during rotation

    # --- signed media URLs (gallery photos) ---
    media_secret: str                      # required
    media_url_ttl: int = 86400             # 24h

    #: Biggest document, in megabytes. 25 was hard-coded and there was no way to
    #: raise it -- which is a strange thing to impose on somebody storing their
    #: own files on their own computer. A scanned passport or a year of bank
    #: statements goes past it easily, and the refusal said only "max 25 MB".
    #:
    #: 500 is not a technical ceiling either; it is a guard against a mistyped
    #: upload filling the disk. Raise DOCUMENT_MAX_MB in backend/.env if a real
    #: file needs more.
    document_max_mb: int = 500

    # --- web push (daily digest). Blank keys simply disable the feature. ---
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@finmate.local"

    # Path to the Firebase service-account JSON, for pushing to the phone app.
    #
    # Web push (VAPID) above reaches browsers and the installed PWA; it cannot
    # reach a native app, because Apple and Google only deliver to those through
    # APNs and FCM. Empty means no phone push, which is a perfectly reasonable
    # way to run this — the phone schedules its own reminders locally either
    # way. See fcm.py for what actually leaves the machine.
    fcm_service_account: str = ""

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    # --- licensing ------------------------------------------------------------
    # Two distinct roles, and a build should never hold both:
    #   publisher  — the machine that issues licences, holds the PRIVATE key.
    #   customer   — a copy handed to someone else; holds only the PUBLIC key,
    #                its own licence file, and licensed_mode on.
    # Shipping the private key inside a customer build would let them mint their
    # own licences, so the bundler strips it deliberately.
    license_signing_key_hex: str = ""      # publisher only, 32 bytes hex
    license_public_key_hex: str = ""       # both sides; safe to ship
    licensed_mode: bool = False            # True in a copy that must hold a licence
    license_file: str = "data/licence.key"  # relative paths resolve against backend/
    # Where a customer copy asks whether its licence still stands. Blank disables
    # the check and the offline expiry is the only gate.
    license_check_url: str = ""
    # How often a licensed copy asks whether it is still allowed to run. Fifteen
    # minutes is the gap between an admin suspending a copy and that copy
    # noticing, once it has internet. Cheap: one small request per interval.
    licence_poll_minutes: int = 15
    # How close to expiry a copy has to be before it starts checking regularly.
    # Outside this window it sleeps -- see main.py::_wait_seconds().
    licence_watch_days: int = 7

    @property
    def is_publisher(self) -> bool:
        return bool(self.license_signing_key_hex)

    @property
    def license_path(self) -> Path:
        p = Path(self.license_file)
        return p if p.is_absolute() else BACKEND_DIR / p

    face_service_url: str = "http://127.0.0.1:8090"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- optional: let an admin purge the CDN edge cache from inside the app ---
    # Needs a Cloudflare API token with Zone → Cache Purge → Purge. Left blank the
    # feature simply doesn't appear.
    cf_api_token: str = ""
    cf_zone_id: str = ""
    # Needed to create tunnels for licensed copies: tunnels belong to an account,
    # not to a zone, so the zone id alone is not enough.
    cf_account_id: str = ""
    # Empty by default, and it must stay that way. This was hard-coded to the
    # publisher's own domain, so every customer's copy -- on their computer, in
    # their Profile screen -- reported the publisher's private address as "your
    # web address". It is baked into the compiled build, so no amount of fixing
    # the launcher's environment could override it. A copy has no public address
    # until somebody sets one.
    public_base_url: str = ""

    # The domain customer subdomains hang off — "meera.example.com". Defaults to
    # whatever public_base_url is under, so a single-domain setup needs no extra
    # configuration.
    licence_domain: str = ""

    @field_validator("licence_domain")
    @classmethod
    def _default_licence_domain(cls, v: str, info) -> str:
        if v.strip():
            return v.strip().lstrip(".").lower()
        base = (info.data.get("public_base_url") or "").split("//")[-1].split("/")[0]
        parts = base.split(".")
        # safenest.raghudarshan.online -> raghudarshan.online
        return ".".join(parts[-2:]).lower() if len(parts) > 2 else base.lower()

    @property
    def licence_hosting_enabled(self) -> bool:
        return bool(self.cf_api_token and self.cf_account_id and self.cf_zone_id)

    @property
    def cdn_purge_enabled(self) -> bool:
        return bool(self.cf_api_token and self.cf_zone_id)

    @field_validator("jwt_secret", "media_secret")
    @classmethod
    def _strong_secret(cls, v: str, info) -> str:
        if v in _BURNED:
            raise ValueError(
                f"{info.field_name} is a known placeholder from an earlier build. "
                "Generate a new one: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if len(v) < 32:
            raise ValueError(f"{info.field_name} must be at least 32 characters")
        return v

    @field_validator("vault_key_hex")
    @classmethod
    def _valid_vault_key(cls, v: str) -> str:
        if v in _BURNED:
            raise ValueError(
                "vault_key_hex is the placeholder key shipped in earlier builds. Generate a new "
                "one (python -c \"import secrets; print(secrets.token_hex(32))\") and move the old "
                "value to VAULT_KEY_LEGACY_HEX so existing items are re-encrypted on startup."
            )
        try:
            raw = bytes.fromhex(v)
        except ValueError:
            raise ValueError("vault_key_hex must be hex-encoded")
        if len(raw) != 32:
            raise ValueError("vault_key_hex must be exactly 32 bytes (64 hex chars)")
        return v

    @field_validator("vault_key_legacy_hex")
    @classmethod
    def _valid_legacy_key(cls, v: str) -> str:
        if v and len(bytes.fromhex(v)) != 32:
            raise ValueError("vault_key_legacy_hex must be exactly 32 bytes (64 hex chars)")
        return v

    @field_validator("db_engine")
    @classmethod
    def _known_engine(cls, v: str) -> str:
        v = (v or "mysql").strip().lower()
        if v not in ("mysql", "sqlite"):
            raise ValueError("db_engine must be 'mysql' or 'sqlite'")
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.db_engine == "sqlite"

    @property
    def sqlite_path(self) -> Path:
        p = Path(self.db_file)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def database_url(self) -> str:
        if self.is_sqlite:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{self.sqlite_path.as_posix()}"
        if not self.db_password:
            raise ValueError("db_password is required when db_engine is 'mysql'")
        pwd = quote_plus(self.db_password)
        return (f"mysql+pymysql://{self.db_user}:{pwd}@{self.db_host}:{self.db_port}"
                f"/{self.db_name}?charset=utf8mb4")


try:
    settings = Settings()
except Exception as exc:  # pragma: no cover - startup guard
    raise SystemExit(
        # Not the app's name: this fires before the database is open, and the name
        # lives in the database. Naming the file that needs fixing is more use to
        # whoever is reading it anyway.
        f"\n[config] Configuration error: {exc}\n\n"
        f"Create {BACKEND_DIR / '.env'} from .env.example and fill in every secret.\n"
    )
