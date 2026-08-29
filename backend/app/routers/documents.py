"""Secure document locker — ID cards, policies, certificates, etc.

Files are stored in a PRIVATE directory that is NOT mounted at /uploads, and are
served only through authenticated, ownership-checked streaming endpoints. Accepts
images and PDFs, with size/type validation and image thumbnails."""
import io
import os
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import func
from sqlalchemy.orm import Session

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from .. import ist, ocr
from .. import storage
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import Document, User
from ..security import guard

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Files live under private/documents/<user_id>/{original,thumb}/ — see app/storage.py.
# Never under a static mount; every read goes through the endpoints below.


def doc_path(d: Document, variant: str) -> str:
    """Absolute path of one variant, derived from the owning row."""
    name = d.filename if variant == storage.ORIGINAL else f"{d.filename}.jpg"
    return storage.media_path(storage.DOCUMENTS, d.user_id, variant, name)


THUMB_MAX = 480
# Per document, from settings so it can be raised without a new build. It was
# 25 MB hard-coded, which a scanned passport or a year of statements goes past
# without trying -- and the refusal said only "max 25 MB", with nothing to
# change. See config.document_max_mb.
# 0 disables the cap entirely, which is the default. See config.document_max_mb.
MAX_BYTES = settings.document_max_mb * 1024 * 1024


def _too_big(what: str = "File") -> str:
    """The refusal, quoting the real limit rather than one frozen at build time."""
    return f"{what} too large (max {settings.document_max_mb} MB)"
CATEGORIES = ["id", "financial", "medical", "property", "vehicle", "education", "insurance", "other"]
IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "bmp"}

# Any file may be stored — a spreadsheet, a Word document, a zip of scans. What
# varies is how it is SERVED, and that is the whole security question here.
#
# INLINE_MIME lists the only types the browser is allowed to render in place. The
# stored MIME comes from this table and never from the client's Content-Type,
# because a file that renders as HTML or SVG would run script on our own origin
# and read everything the signed-in user can. Everything outside this table is
# sent as a download with a neutral type, which is inert whatever it contains.
INLINE_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp",
    "heic": "image/heic", "heif": "image/heif", "pdf": "application/pdf",
}
SAFE_MIME = INLINE_MIME          # kept for older callers

# Named only so the file downloads with a sensible type; none of these are ever
# rendered in place.
DOWNLOAD_MIME = {
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv", "txt": "text/plain", "rtf": "application/rtf",
    "zip": "application/zip", "rar": "application/vnd.rar", "7z": "application/x-7z-compressed",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "json": "application/json", "xml": "application/xml",
    "mp3": "audio/mpeg", "mp4": "video/mp4", "mov": "video/quicktime",
}

# Extensions refused outright: executables and scripts. Storing them offers no
# benefit to a documents feature and turns a shared account into a delivery route.
BLOCKED_EXT = {"exe", "com", "bat", "cmd", "msi", "scr", "pif", "cpl", "jar",
               "app", "dmg", "pkg", "sh", "bash", "zsh", "ps1", "vbs", "js",
               "jse", "wsf", "wsh", "hta", "reg", "dll", "so", "dylib"}

MAX_NAME = 200


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read an upload into memory, aborting once it exceeds `limit`."""
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        # limit <= 0 is "no limit". The owner asked for no ceiling on their own
        # files, and an invented one is not something they can argue with.
        if limit > 0 and total > limit:
            raise HTTPException(413, _too_big())
        chunks.append(chunk)
    return b"".join(chunks)


def _fmt(d) -> str | None:
    return d.strftime("%d-%m-%Y") if d else None


def _parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _present(d: Document) -> dict:
    days = (d.expiry_date - ist.today()).days if d.expiry_date else None
    status = None
    if days is not None:
        status = "expired" if days < 0 else "soon" if days <= 30 else "ok"
    return {
        "id": d.id,
        "title": d.title,
        "category": d.category,
        "doc_number": d.doc_number,
        "issue_date": d.issue_date.isoformat() if d.issue_date else None,
        "issue_fmt": _fmt(d.issue_date),
        "expiry_date": d.expiry_date.isoformat() if d.expiry_date else None,
        "expiry_fmt": _fmt(d.expiry_date),
        "days_until_expiry": days,
        "expiry_status": status,
        "notes": d.notes,
        "ext": d.ext,
        "mime": d.mime,
        "is_pdf": d.ext == "pdf",
        # Only an image can be shown in an <img>. Everything else — spreadsheets,
        # Word files, archives — gets the download card instead of a broken picture.
        "is_image": d.ext in IMAGE_EXT,
        "size_bytes": d.size_bytes,
        "pages": int(d.pages or 1),
        "file_url": f"/api/documents/{d.id}/file",
        "thumb_url": f"/api/documents/{d.id}/thumb" if d.has_thumb else None,
        "is_favourite": int(d.is_favorite or 0),
        "is_trashed": int(d.is_trashed or 0),
        "trashed_fmt": _fmt(d.trashed_at) if d.trashed_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("")
def index(category: str = "", q: str = "", fav: int = 0,
          user: User = Depends(guard("documents", "view")), db: Session = Depends(get_db)):
    # Trashed documents are hidden everywhere except the recycle bin below.
    base = db.query(Document).filter(Document.user_id == user.id, Document.is_trashed == 0)
    query = base
    if category and category in CATEGORIES:
        query = query.filter(Document.category == category)
    if fav:
        query = query.filter(Document.is_favorite == 1)
    if q:
        like = f"%{q.strip()}%"
        # ocr_text included so a search reaches INSIDE the scan: the account
        # number printed on a bill nobody ever typed into the form.
        query = query.filter((Document.title.like(like)) | (Document.doc_number.like(like))
                             | (Document.notes.like(like)) | (Document.ocr_text.like(like)))
    rows = query.order_by(Document.is_favorite.desc(), Document.created_at.desc(),
                          Document.id.desc()).all()
    # per-category counts (ignores current filter, for the chip badges)
    counts = dict(db.query(Document.category, func.count(Document.id))
                  .filter(Document.user_id == user.id, Document.is_trashed == 0)
                  .group_by(Document.category).all())
    return {
        "items": [_present(d) for d in rows],
        "total": base.count(),
        "counts": {c: int(counts.get(c, 0)) for c in CATEGORIES},
        "trashed": db.query(Document).filter(Document.user_id == user.id,
                                             Document.is_trashed == 1).count(),
    }


# ---------------------------------------------------------------- recycle bin

@router.get("/trash")
def trash_list(user: User = Depends(guard("documents", "view")), db: Session = Depends(get_db)):
    rows = (db.query(Document)
            .filter(Document.user_id == user.id, Document.is_trashed == 1)
            .order_by(Document.trashed_at.desc(), Document.id.desc()).all())
    return {"items": [_present(d) for d in rows], "total": len(rows)}


@router.post("/trash/empty")
def empty_trash(user: User = Depends(guard("documents", "delete")), db: Session = Depends(get_db)):
    """Permanently delete every trashed document and its files."""
    rows = (db.query(Document)
            .filter(Document.user_id == user.id, Document.is_trashed == 1).all())
    for d in rows:
        _delete_files(d)
        db.delete(d)
    db.commit()
    audit(db, user.id, "empty_trash", "document", None, {"deleted": len(rows)})
    return {"deleted": len(rows)}


@router.post("")
async def create(file: UploadFile = File(...), title: str = Form(""), category: str = Form("other"),
                 doc_number: str = Form(""), issue_date: str = Form(""), expiry_date: str = Form(""),
                 notes: str = Form(""),
                 user: User = Depends(guard("documents", "create")), db: Session = Depends(get_db)):
    raw = await _read_capped(file, MAX_BYTES)
    if not raw:
        raise HTTPException(400, "Empty file")

    ext = (os.path.splitext(file.filename or "")[1].lstrip(".") or "").lower()[:12]
    if ext == "jpe":
        ext = "jpg"
    if not ext:
        ext = "bin"
    if ext in BLOCKED_EXT:
        raise HTTPException(415, "Programs and scripts can't be stored as documents.")

    if category not in CATEGORIES:
        category = "other"

    # Only the types that get rendered in place need their bytes checked, because
    # only those can be made to run something. A spreadsheet is sent as a download
    # and is inert no matter what is inside it, so demanding it prove its format
    # would reject perfectly good files for no gain.
    if ext == "pdf":
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(415, "That file isn't a valid PDF")
    elif ext in IMAGE_EXT:
        try:
            Image.open(io.BytesIO(raw)).verify()
        except Exception:
            raise HTTPException(415, "That file isn't a valid image")

    # MIME comes from our allowlist only — never from the client's Content-Type.
    mime = INLINE_MIME.get(ext) or DOWNLOAD_MIME.get(ext) or "application/octet-stream"

    stored = f"{uuid.uuid4().hex}.{ext}"
    storage.save(storage.DOCUMENTS, user.id, storage.ORIGINAL, stored, raw)

    # Thumbnail for images (best-effort). PDFs fall back to a UI icon.
    has_thumb = 0
    if ext in IMAGE_EXT:
        try:
            pil = Image.open(io.BytesIO(raw))
            pil = pil.convert("RGB") if pil.mode not in ("RGB", "L") else pil
            pil.thumbnail((THUMB_MAX, THUMB_MAX))
            tbuf = io.BytesIO(); pil.save(tbuf, format="JPEG", quality=80)
            storage.save(storage.DOCUMENTS, user.id, storage.THUMB, f"{stored}.jpg", tbuf.getvalue())
            has_thumb = 1
        except Exception:
            has_thumb = 0

    now = ist.now()
    doc = Document(
        user_id=user.id, title=(title.strip() or os.path.splitext(file.filename or "")[0] or "Document"),
        category=category, doc_number=doc_number.strip() or None,
        issue_date=_parse_date(issue_date), expiry_date=_parse_date(expiry_date),
        notes=notes.strip() or None, filename=stored, orig_name=file.filename,
        mime=mime, ext=ext, size_bytes=len(raw), has_thumb=has_thumb, is_favorite=0,
        created_at=now, updated_at=now,
    )
    db.add(doc); db.commit(); db.refresh(doc)
    audit(db, user.id, "create", "document", doc.id, {"label": doc.title, "category": category})
    return {"item": _present(doc)}


@router.post("/scan")
async def scan(files: list[UploadFile] = File(...), title: str = Form(""),
               category: str = Form("other"), doc_number: str = Form(""),
               issue_date: str = Form(""), expiry_date: str = Form(""), notes: str = Form(""),
               user: User = Depends(guard("documents", "create")), db: Session = Depends(get_db)):
    """Assemble captured page images into a single multi-page PDF.

    The client sends already-enhanced JPEGs (one per page, in order); Pillow writes
    them into one PDF so a scanned passport or agreement stays one document rather
    than a pile of loose photos."""
    if not files:
        raise HTTPException(400, "No pages captured")
    if len(files) > 30:
        raise HTTPException(413, "Too many pages (max 30)")

    pages, total = [], 0
    for f in files:
        raw = await _read_capped(f, MAX_BYTES)
        total += len(raw)
        if MAX_BYTES > 0 and total > MAX_BYTES:
            raise HTTPException(413, _too_big("Scan"))
        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
        except Exception:
            raise HTTPException(415, "One of the pages isn't a readable image")
        pages.append(im.convert("RGB"))

    if category not in CATEGORIES:
        category = "other"

    stored = f"{uuid.uuid4().hex}.pdf"
    pdf = io.BytesIO()
    pages[0].save(pdf, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    data = pdf.getvalue()
    if MAX_BYTES > 0 and len(data) > MAX_BYTES:
        raise HTTPException(413, _too_big("Scan"))
    storage.save(storage.DOCUMENTS, user.id, storage.ORIGINAL, stored, data)

    # First page doubles as the thumbnail, so scans look like everything else.
    has_thumb = 0
    try:
        cover = pages[0].copy()
        cover.thumbnail((THUMB_MAX, THUMB_MAX))
        tbuf = io.BytesIO(); cover.save(tbuf, format="JPEG", quality=80)
        storage.save(storage.DOCUMENTS, user.id, storage.THUMB, f"{stored}.jpg", tbuf.getvalue())
        has_thumb = 1
    except Exception:
        has_thumb = 0

    now = ist.now()
    doc = Document(
        user_id=user.id, title=(title.strip() or f"Scan {now.strftime('%d-%m-%Y')}"),
        category=category, doc_number=doc_number.strip() or None,
        issue_date=_parse_date(issue_date), expiry_date=_parse_date(expiry_date),
        notes=notes.strip() or None, filename=stored, orig_name=f"{title.strip() or 'scan'}.pdf",
        mime="application/pdf", ext="pdf", size_bytes=len(data), has_thumb=has_thumb,
        is_favorite=0, pages=len(pages), is_trashed=0, created_at=now, updated_at=now,
    )
    db.add(doc); db.commit(); db.refresh(doc)
    audit(db, user.id, "scan", "document", doc.id,
          {"label": doc.title, "pages": len(pages), "category": category})
    return {"item": _present(doc)}


def _owned(db: Session, uid: int, doc_id: int) -> Document:
    d = db.query(Document).filter(Document.id == doc_id, Document.user_id == uid).first()
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@router.get("/{id}/suggestions")
def suggestions(id: int, user: User = Depends(guard("documents", "view")),
                db: Session = Depends(get_db)):
    """What the text in this document says its fields should be.

    Returned for confirmation, never written. The values come from a reader that
    is right most of the time, and a wrong expiry date that filled itself in is
    worse than an empty one — nobody re-checks a field they did not type.
    """
    d = _owned(db, user.id, id)
    if d.ocr_at is None:
        return {"ready": False, "reason": "not read yet", "has_text": False}
    if not d.ocr_text:
        return {"ready": True, "has_text": False, "fields": {}, "text": ""}

    found = ocr.extract(d.ocr_text)
    # Only offer what is not already filled in — replacing something the owner
    # typed with a guess is exactly the behaviour people distrust.
    fields = {}
    if found["expiry_date"] and not d.expiry_date:
        fields["expiry_date"] = ocr.iso(found["expiry_date"])
    if found["issue_date"] and not d.issue_date:
        fields["issue_date"] = ocr.iso(found["issue_date"])
    if found["doc_number"] and not d.doc_number:
        fields["doc_number"] = found["doc_number"]
    return {
        "ready": True, "has_text": True, "fields": fields,
        "amounts": found["amounts"],
        "dates": [ocr.iso(x) for x in found["dates"]],
        "text": d.ocr_text,
        "preview": ocr.summarise(d.ocr_text),
    }


@router.get("/{id}/file")
def get_file(id: int, user: User = Depends(guard("documents", "view")), db: Session = Depends(get_db)):
    d = _owned(db, user.id, id)
    path = doc_path(d, storage.ORIGINAL)
    if not os.path.exists(path):
        raise HTTPException(404, "File missing")
    # Re-derive the type from the stored extension rather than trusting the `mime`
    # column, which on rows created by older builds came from the uploader.
    #
    # Anything outside INLINE_MIME is forced to download. Serving an arbitrary file
    # inline is how a stored .html or .svg turns into script running on this origin,
    # with access to everything the signed-in user has. A download is inert.
    inline = d.ext in INLINE_MIME
    return FileResponse(
        path,
        media_type=INLINE_MIME[d.ext] if inline else "application/octet-stream",
        filename=d.orig_name or f"document.{d.ext}",
        content_disposition_type="inline" if inline else "attachment")


@router.get("/{id}/thumb")
def get_thumb(id: int, user: User = Depends(guard("documents", "view")), db: Session = Depends(get_db)):
    d = _owned(db, user.id, id)
    if not d.has_thumb:
        raise HTTPException(404, "No thumbnail")
    path = doc_path(d, storage.THUMB)
    if not os.path.exists(path):
        raise HTTPException(404, "No thumbnail")
    return FileResponse(path, media_type="image/jpeg", content_disposition_type="inline")


@router.put("/{id}")
def update(id: int, body: dict = Body(...),
           user: User = Depends(guard("documents", "edit")), db: Session = Depends(get_db)):
    d = _owned(db, user.id, id)
    if "title" in body:
        d.title = (body["title"] or "").strip() or d.title
    if "category" in body and body["category"] in CATEGORIES:
        d.category = body["category"]
    if "doc_number" in body:
        d.doc_number = (body["doc_number"] or "").strip() or None
    if "issue_date" in body:
        d.issue_date = _parse_date(body["issue_date"])
    if "expiry_date" in body:
        d.expiry_date = _parse_date(body["expiry_date"])
    if "notes" in body:
        d.notes = (body["notes"] or "").strip() or None
    d.updated_at = ist.now()
    db.commit()
    audit(db, user.id, "update", "document", id, {"label": d.title})
    return {"item": _present(d)}


@router.post("/{id}/favourite")
def favourite(id: int, user: User = Depends(guard("documents", "edit")), db: Session = Depends(get_db)):
    d = _owned(db, user.id, id)
    d.is_favorite = 0 if d.is_favorite else 1
    db.commit()
    return {"id": id, "is_favourite": int(d.is_favorite)}


def _delete_files(d: Document) -> None:
    storage.remove(storage.DOCUMENTS, d.user_id, storage.ORIGINAL, d.filename)
    storage.remove(storage.DOCUMENTS, d.user_id, storage.THUMB, f"{d.filename}.jpg")


@router.delete("/{id}")
def destroy(id: int, user: User = Depends(guard("documents", "delete")), db: Session = Depends(get_db)):
    """Move to the recycle bin. Files stay on disk until the bin is emptied, so a
    mis-tap on an irreplaceable ID scan is recoverable."""
    d = _owned(db, user.id, id)
    d.is_trashed = 1
    d.trashed_at = ist.now()
    db.commit()
    audit(db, user.id, "trash", "document", id, {"label": d.title})
    return {"trashed": id}


@router.post("/{id}/restore")
def restore(id: int, user: User = Depends(guard("documents", "edit")), db: Session = Depends(get_db)):
    d = _owned(db, user.id, id)
    d.is_trashed = 0
    d.trashed_at = None
    db.commit()
    audit(db, user.id, "restore", "document", id, {"label": d.title})
    return {"item": _present(d)}


@router.delete("/{id}/permanent")
def destroy_permanent(id: int, user: User = Depends(guard("documents", "delete")), db: Session = Depends(get_db)):
    """Irreversible: removes the row and both stored files. Only from the bin."""
    d = _owned(db, user.id, id)
    if not d.is_trashed:
        raise HTTPException(422, "Move the document to the recycle bin first")
    label = d.title
    _delete_files(d)
    db.delete(d); db.commit()
    audit(db, user.id, "delete", "document", id, {"label": label})
    return {"deleted": id}
