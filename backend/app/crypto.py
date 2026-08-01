"""Vault encryption (AES-256-GCM) with support for key rotation.

`encrypt` always uses the current key. `decrypt` tries the current key first and
falls back to the legacy key, so rotating VAULT_KEY_HEX never locks users out of
existing items — `reencrypt_legacy_items` (run at startup) moves them across.
"""
import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

_KEY = bytes.fromhex(settings.vault_key_hex)
_LEGACY = bytes.fromhex(settings.vault_key_legacy_hex) if settings.vault_key_legacy_hex else None


def encrypt(plain: str) -> str:
    """AES-256-GCM → base64(nonce + ciphertext), using the current key."""
    nonce = os.urandom(12)
    ct = AESGCM(_KEY).encrypt(nonce, plain.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def encrypt_with(key_hex: str, plain: str) -> str:
    """Encrypt under a caller-supplied key rather than this server's.

    Used when building a personal export: the copy gets a brand-new vault key, so
    the shared key that protects everyone else's secrets never leaves this machine.
    """
    nonce = os.urandom(12)
    ct = AESGCM(bytes.fromhex(key_hex)).encrypt(nonce, plain.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _decrypt_with(key: bytes, token: str) -> str:
    raw = base64.b64decode(token)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def decrypt(token: str) -> str:
    """Decrypt with the current key, falling back to the legacy key mid-rotation."""
    try:
        return _decrypt_with(_KEY, token)
    except (InvalidTag, ValueError):
        if _LEGACY is None:
            raise
        return _decrypt_with(_LEGACY, token)


def needs_rotation(token: str) -> bool:
    """True when `token` is readable only with the legacy key."""
    if _LEGACY is None or not token:
        return False
    try:
        _decrypt_with(_KEY, token)
        return False
    except (InvalidTag, ValueError):
        try:
            _decrypt_with(_LEGACY, token)
            return True
        except Exception:
            return False  # unreadable by either key — leave it alone


def reencrypt_legacy_items(db) -> int:
    """Re-encrypt every vault secret still under the legacy key. Idempotent;
    returns how many rows were moved. Rows readable by neither key are skipped."""
    if _LEGACY is None:
        return 0
    from .models import VaultItem

    moved = 0
    for row in db.query(VaultItem).all():
        for field in ("password_enc", "notes_enc"):
            token = getattr(row, field)
            if token and needs_rotation(token):
                setattr(row, field, encrypt(_decrypt_with(_LEGACY, token)))
                moved += 1
    if moved:
        db.commit()
    return moved
