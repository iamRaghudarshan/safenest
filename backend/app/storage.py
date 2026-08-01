"""On-disk layout for user-uploaded media.

Everything lives under `backend/private/`, partitioned by module, then by owner,
then by variant:

    private/gallery/<user_id>/original/<uuid>.jpg
    private/gallery/<user_id>/thumb/<uuid>.jpg
    private/documents/<user_id>/original/<uuid>.pdf
    private/documents/<user_id>/thumb/<uuid>.jpg

Why this shape:
  * per-user directories keep one account's files together, so "export/delete
    everything for this user" is a directory operation rather than a table scan,
    and one user's volume never degrades lookups for another's;
  * separating originals from derivatives means thumbnails can be regenerated or
    purged wholesale without touching a single irreplaceable original;
  * directories stay small enough to list quickly — the old flat folder held every
    user's originals AND thumbnails together (10k+ entries and growing).

The database stores only the bare filename; the directory is derived from the
owning row, so nothing in a request path is ever used to build a filesystem path.
"""
import os
import shutil

from .config import BACKEND_DIR, settings

# Defaults to backend/private. MEDIA_ROOT overrides it so the portable bundle can
# keep the database and the media tree together in one data folder that travels.
PRIVATE_ROOT = (
    os.path.abspath(os.path.join(str(BACKEND_DIR), settings.media_root))
    if settings.media_root else os.path.join(str(BACKEND_DIR), "private")
)

GALLERY = "gallery"
DOCUMENTS = "documents"
AVATARS = "avatars"

ORIGINAL = "original"
THUMB = "thumb"
VARIANTS = (ORIGINAL, THUMB)


def is_safe_name(name: str) -> bool:
    """A stored filename must be a bare basename — no separators, no traversal."""
    return bool(name) and "/" not in name and "\\" not in name and os.path.basename(name) == name and name not in (".", "..")


def media_dir(module: str, user_id: int, variant: str) -> str:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    return os.path.join(PRIVATE_ROOT, module, str(int(user_id)), variant)


def media_path(module: str, user_id: int, variant: str, name: str) -> str:
    if not is_safe_name(name):
        raise ValueError("unsafe filename")
    return os.path.join(media_dir(module, user_id, variant), name)


def ensure_dirs(module: str, user_id: int) -> None:
    for v in VARIANTS:
        os.makedirs(media_dir(module, user_id, v), exist_ok=True)


def save(module: str, user_id: int, variant: str, name: str, data: bytes) -> str:
    path = media_path(module, user_id, variant, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def remove(module: str, user_id: int, variant: str, name: str) -> bool:
    """Delete one file, ignoring a missing one. Returns whether it existed."""
    try:
        os.remove(media_path(module, user_id, variant, name))
        return True
    except (OSError, ValueError):
        return False


def purge_user(module: str, user_id: int) -> None:
    """Drop every stored file for one user in one module (account deletion)."""
    shutil.rmtree(os.path.join(PRIVATE_ROOT, module, str(int(user_id))), ignore_errors=True)


def usage(module: str, user_id: int) -> dict:
    """Per-user file count and byte total, for storage reporting."""
    out = {"files": 0, "bytes": 0}
    for v in VARIANTS:
        d = media_dir(module, user_id, v)
        if not os.path.isdir(d):
            continue
        with os.scandir(d) as it:
            for e in it:
                if e.is_file():
                    out["files"] += 1
                    out["bytes"] += e.stat().st_size
    return out


MODULES = (GALLERY, DOCUMENTS, AVATARS)


def usage_for(user_id: int) -> dict:
    """Everything one user is storing, split by module."""
    uid = int(user_id)
    return _measure(f"user:{uid}",
                    {m: os.path.join(PRIVATE_ROOT, m, str(uid)) for m in MODULES})


def _tree_signature(roots: list[str]) -> tuple:
    """A cheap fingerprint of the media tree.

    A directory's timestamp moves whenever a file is added to or removed from it,
    and FinMate only ever writes a file once — nothing is rewritten in place. So
    the directory timestamps alone say whether the totals can still be trusted.
    There are a few dozen directories against tens of thousands of files, which is
    the difference between a few milliseconds and well over a second.
    """
    sig = []
    for base in roots:
        for dirpath, _dirnames, _filenames in os.walk(base):
            try:
                sig.append((dirpath, os.stat(dirpath).st_mtime_ns))
            except OSError:
                pass
    return tuple(sorted(sig))


_cache: dict[str, tuple] = {}


def _measure(key: str, roots: dict[str, str]) -> dict:
    """Add up the files under each named root, reusing the last answer if nothing moved.

    Never a timed cache: uploading thirty photos and opening this screen a second
    later has to show thirty photos, not a figure that expires in a minute.
    """
    sig = _tree_signature(list(roots.values()))
    hit = _cache.get(key)
    if hit and hit[0] == sig:
        return hit[1]

    per: dict[str, dict] = {}
    for module, base in roots.items():
        files = total = 0
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                    files += 1
                except OSError:
                    pass          # vanished mid-walk; not worth failing the report
        per[module] = {"files": files, "bytes": total}

    out = {
        "modules": per,
        "files": sum(x["files"] for x in per.values()),
        "bytes": sum(x["bytes"] for x in per.values()),
    }
    _cache[key] = (sig, out)
    return out


def usage_everyone() -> dict:
    """Every user's files, split by module.

    Walks the tree rather than summing the size_bytes columns: those record the
    original upload only, so they miss every thumbnail and every avatar, and they
    drift from reality whenever a file is removed outside the app. Disk is the
    only thing that can answer "how much room is this actually taking".
    """
    return _measure("all", {m: os.path.join(PRIVATE_ROOT, m) for m in MODULES})


def disk_space() -> dict:
    """Free and total space on whatever drive the media lives on."""
    try:
        total, _used, free = shutil.disk_usage(PRIVATE_ROOT)
        return {"free": free, "total": total}
    except OSError:
        return {"free": 0, "total": 0}
