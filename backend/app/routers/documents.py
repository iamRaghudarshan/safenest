"""Secure document locker — ID cards, policies, certificates, etc.

Files are stored in a PRIVATE directory that is NOT mounted at /uploads, and are
served only through authenticated, ownership-checked streaming endpoints. Accepts
images and PDFs, with size/type validation and image thumbnails."""
import hashlib
import io
import os
import uuid
from datetime import date, datetime, timedelta

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

from .. import doctype, ist, ocr
from .. import storage
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import Document, DocumentFolder, DocumentVersion, User
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
        # What the classifier read this as, and who decided. The UI needs both:
        # a suggestion is offered for correction, a correction is not.
        "kind": d.kind,
        "kind_source": d.kind_source,
        "folder_id": d.folder_id,
        "is_trashed": int(d.is_trashed or 0),
        "trashed_fmt": _fmt(d.trashed_at) if d.trashed_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("")
def index(category: str = "", q: str = "", fav: int = 0, folder: str = "",
          sort: str = "",
          user: User = Depends(guard("documents", "view")), db: Session = Depends(get_db)):
    # Trashed documents are hidden everywhere except the recycle bin below.
    base = db.query(Document).filter(Document.user_id == user.id, Document.is_trashed == 0)
    query = base

    # Folder scoping. `folder=` (absent) means "everything, wherever it is",
    # which is what search and the category chips want. `folder=0` means the
    # top level specifically. A number means inside that folder.
    #
    # The distinction matters: browsing a tree must NOT show you the whole
    # library flattened, and searching must NOT be limited to the folder you
    # happen to be standing in. They are different questions and the old single
    # endpoint could only ask one of them.
    in_folder = None
    if folder != "":
        try:
            in_folder = int(folder)
        except ValueError:
            in_folder = 0
        query = query.filter(Document.folder_id.is_(None) if in_folder == 0
                             else Document.folder_id == in_folder)

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
    # Sort like a file manager. Favourites still float on the default, because
    # that is what the star is for; choosing an explicit order turns that off,
    # since someone who asked for "by name" means by name.
    key = (sort or "").strip().lower()
    if key == "name":
        order = [Document.title.asc(), Document.id.asc()]
    elif key == "oldest":
        order = [Document.created_at.asc(), Document.id.asc()]
    elif key == "largest":
        order = [Document.size_bytes.desc(), Document.id.desc()]
    elif key == "smallest":
        order = [Document.size_bytes.asc(), Document.id.asc()]
    else:
        order = [Document.is_favorite.desc(), Document.created_at.desc(),
                 Document.id.desc()]
    rows = query.order_by(*order).all()

    # The folders sitting alongside these files, when browsing a tree.
    folders = []
    if in_folder is not None:
        fq = (db.query(DocumentFolder)
              .filter(DocumentFolder.user_id == user.id,
                      DocumentFolder.is_trashed == 0)
              .filter(DocumentFolder.parent_id.is_(None) if in_folder == 0
                      else DocumentFolder.parent_id == in_folder)
              .order_by(DocumentFolder.name.asc()))
        folders = [_folder(db, f) for f in fq.all()]
    # per-category counts (ignores current filter, for the chip badges)
    counts = dict(db.query(Document.category, func.count(Document.id))
                  .filter(Document.user_id == user.id, Document.is_trashed == 0)
                  .group_by(Document.category).all())
    return {
        "items": [_present(d) for d in rows],
        "folders": folders,
        "path": _breadcrumb(db, user.id, in_folder) if in_folder else [],
        "total": base.count(),
        "counts": {c: int(counts.get(c, 0)) for c in CATEGORIES},
        "trashed": db.query(Document).filter(Document.user_id == user.id,
                                             Document.is_trashed == 1).count(),
    }


# -------------------------------------------------------------------- folders

def _folder(db: Session, f: DocumentFolder) -> dict:
    """One folder, with the counts a file manager shows on the tile."""
    docs = (db.query(func.count(Document.id))
            .filter(Document.user_id == f.user_id, Document.folder_id == f.id,
                    Document.is_trashed == 0).scalar() or 0)
    subs = (db.query(func.count(DocumentFolder.id))
            .filter(DocumentFolder.user_id == f.user_id,
                    DocumentFolder.parent_id == f.id,
                    DocumentFolder.is_trashed == 0).scalar() or 0)
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id,
            "documents": int(docs), "folders": int(subs),
            "updated_at": f.updated_at.isoformat() if f.updated_at else None}


def _breadcrumb(db: Session, uid: int, fid: int | None) -> list:
    """Top-level-downwards path to `fid`.

    Walks parents rather than reading a stored path, and stops after 64 hops.
    The move endpoint refuses to build a cycle, but a cycle that somehow
    existed would otherwise hang this loop forever and take the request thread
    with it — a bound costs nothing and turns a hang into a short path.
    """
    out = []
    seen = set()
    cur = fid
    for _ in range(64):
        if not cur or cur in seen:
            break
        seen.add(cur)
        f = (db.query(DocumentFolder)
             .filter(DocumentFolder.id == cur, DocumentFolder.user_id == uid).first())
        if not f:
            break
        out.append({"id": f.id, "name": f.name})
        cur = f.parent_id
    return list(reversed(out))


def _descendants(db: Session, uid: int, fid: int) -> set:
    """Every folder at or below `fid`. Used to refuse a move into itself."""
    out = {fid}
    edge = [fid]
    while edge:
        rows = (db.query(DocumentFolder.id)
                .filter(DocumentFolder.user_id == uid,
                        DocumentFolder.parent_id.in_(edge)).all())
        edge = [r[0] for r in rows if r[0] not in out]
        out.update(edge)
    return out


def _own_folder(db: Session, uid: int, fid: int) -> DocumentFolder:
    f = (db.query(DocumentFolder)
         .filter(DocumentFolder.id == fid, DocumentFolder.user_id == uid).first())
    if not f:
        raise HTTPException(404, "Folder not found")
    return f


@router.get("/folders")
def folder_tree(user: User = Depends(guard("documents", "view")),
                db: Session = Depends(get_db)):
    """The whole tree in one call, for a move dialog's folder picker.

    Small by nature — folders are made by hand, so there are tens of them, not
    the thousands the documents themselves run to.
    """
    rows = (db.query(DocumentFolder)
            .filter(DocumentFolder.user_id == user.id, DocumentFolder.is_trashed == 0)
            .order_by(DocumentFolder.name.asc()).all())
    return {"items": [_folder(db, f) for f in rows]}


@router.post("/folders")
def create_folder(body: dict = Body(...),
                  user: User = Depends(guard("documents", "create")),
                  db: Session = Depends(get_db)):
    name = (body.get("name") or "").strip()[:160]
    if not name:
        raise HTTPException(400, "Folder needs a name")
    parent = body.get("parent_id")
    parent_id = int(parent) if parent else None
    if parent_id:
        _own_folder(db, user.id, parent_id)     # 404s rather than silently re-homing
    clash = (db.query(DocumentFolder)
             .filter(DocumentFolder.user_id == user.id,
                     DocumentFolder.parent_id.is_(None) if parent_id is None
                     else DocumentFolder.parent_id == parent_id,
                     DocumentFolder.name == name,
                     DocumentFolder.is_trashed == 0).first())
    if clash:
        raise HTTPException(409, f"There is already a folder called {name} here")
    now = ist.now()
    f = DocumentFolder(user_id=user.id, parent_id=parent_id, name=name,
                       is_trashed=0, created_at=now, updated_at=now)
    db.add(f); db.commit(); db.refresh(f)
    audit(db, user.id, "create", "folder", f.id, {"label": name})
    return {"item": _folder(db, f)}


@router.put("/folders/{fid}")
def update_folder(fid: int, body: dict = Body(...),
                  user: User = Depends(guard("documents", "edit")),
                  db: Session = Depends(get_db)):
    """Rename and/or move a folder."""
    f = _own_folder(db, user.id, fid)
    if "name" in body:
        name = (body.get("name") or "").strip()[:160]
        if not name:
            raise HTTPException(400, "Folder needs a name")
        f.name = name
    if "parent_id" in body:
        raw = body.get("parent_id")
        new_parent = int(raw) if raw else None
        # The move that destroys a tree: putting a folder inside itself, or
        # inside one of its own children. Both detach the whole subtree from
        # the top level — it still exists, it is simply unreachable, and the
        # breadcrumb walk loops. Refused rather than repaired afterwards.
        if new_parent is not None:
            if new_parent in _descendants(db, user.id, fid):
                raise HTTPException(400, "A folder cannot be moved inside itself")
            _own_folder(db, user.id, new_parent)
        f.parent_id = new_parent
    f.updated_at = ist.now()
    db.commit()
    audit(db, user.id, "update", "folder", f.id, {"label": f.name})
    return {"item": _folder(db, f)}


@router.delete("/folders/{fid}")
def trash_folder(fid: int, user: User = Depends(guard("documents", "delete")),
                 db: Session = Depends(get_db)):
    """Bin a folder and everything under it.

    The whole subtree goes, because a folder whose contents stayed visible at
    the top level would look like the delete silently failed — and a folder
    that vanished while its documents became unreachable would be worse.
    Soft only: restoring is a separate act and nothing is removed from disk.
    """
    f = _own_folder(db, user.id, fid)
    ids = _descendants(db, user.id, fid)
    now = ist.now()
    (db.query(DocumentFolder)
     .filter(DocumentFolder.user_id == user.id, DocumentFolder.id.in_(ids))
     .update({DocumentFolder.is_trashed: 1, DocumentFolder.trashed_at: now},
             synchronize_session=False))
    n = (db.query(Document)
         .filter(Document.user_id == user.id, Document.folder_id.in_(ids),
                 Document.is_trashed == 0)
         .update({Document.is_trashed: 1, Document.trashed_at: now},
                 synchronize_session=False))
    db.commit()
    audit(db, user.id, "trash", "folder", fid,
          {"label": f.name, "documents": int(n), "folders": len(ids)})
    return {"deleted": fid, "documents": int(n), "folders": len(ids)}


@router.post("/move")
def move_documents(body: dict = Body(...),
                   user: User = Depends(guard("documents", "edit")),
                   db: Session = Depends(get_db)):
    """Move documents into a folder (or to the top level with folder_id null)."""
    raw = body.get("ids") or []
    ids = [int(x) for x in raw if str(x).lstrip("-").isdigit()][:2000]
    if not ids:
        return {"moved": 0}
    target = body.get("folder_id")
    folder_id = int(target) if target else None
    if folder_id:
        _own_folder(db, user.id, folder_id)
    n = (db.query(Document)
         .filter(Document.user_id == user.id, Document.id.in_(ids))
         .update({Document.folder_id: folder_id, Document.updated_at: ist.now()},
                 synchronize_session=False))
    db.commit()
    audit(db, user.id, "move", "document", None,
          {"count": int(n), "folder_id": folder_id})
    return {"moved": int(n), "folder_id": folder_id}


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


@router.post("/{id}/kind")
def set_kind(id: int, body: dict = Body(...),
             user: User = Depends(guard("documents", "edit")),
             db: Session = Depends(get_db)):
    """Correct what kind of document this is.

    The classifier suggests; this is how a person disagrees. Recording the
    source as 'user' is what stops the next indexing pass putting the guess
    back — see indexer.index_ocr_doc. Passing null clears the type entirely,
    for the documents that are not any of the kinds on the list.
    """
    d = _owned(db, user.id, id)
    raw = body.get("kind")
    kind = (raw or "").strip().lower() or None
    if kind is not None and kind not in doctype.KINDS:
        raise HTTPException(422, f"Unknown kind: {kind}")
    d.kind = kind
    # A human is certain by definition, and their choice is not a score.
    d.kind_confidence = None
    d.kind_source = "user" if kind else None
    d.updated_at = ist.now()
    db.commit()
    audit(db, user.id, "classify", "document", id, {"kind": kind})
    return {"id": id, "kind": kind, "kind_source": d.kind_source}


@router.get("/kinds")
def kinds(user: User = Depends(guard("documents", "view"))):
    """The types the classifier knows about, for a correction menu."""
    return {"items": list(doctype.KINDS)}


def _copy_file(d: Document, new_name: str) -> None:
    """Duplicate a document's bytes on disk under a new opaque name."""
    src = storage.media_path(storage.DOCUMENTS, d.user_id, storage.ORIGINAL, d.filename)
    with open(src, "rb") as f:
        storage.save(storage.DOCUMENTS, d.user_id, storage.ORIGINAL, new_name, f.read())
    if d.has_thumb:
        tsrc = storage.media_path(storage.DOCUMENTS, d.user_id, storage.THUMB,
                                  f"{d.filename}.jpg")
        if os.path.exists(tsrc):
            with open(tsrc, "rb") as f:
                storage.save(storage.DOCUMENTS, d.user_id, storage.THUMB,
                             f"{new_name}.jpg", f.read())


@router.post("/{id}/copy")
def copy(id: int, body: dict = Body(...),
         user: User = Depends(guard("documents", "create")),
         db: Session = Depends(get_db)):
    """Duplicate a document, optionally into another folder.

    A real copy of the bytes, not a second row pointing at one file. Sharing
    the file would mean deleting either copy destroys both, which is not what
    anybody means by "copy" — and the alternative, reference counting, is a
    lot of machinery to save a few megabytes of paperwork.

    The OCR text and classification come along, because they describe the
    contents and the contents are identical. Re-running them would cost a
    minute and reach the same answer.
    """
    src = _owned(db, user.id, id)
    folder = body.get("folder_id", "keep")
    folder_id = src.folder_id if folder == "keep" else (int(folder) if folder else None)
    if folder_id:
        _own_folder(db, user.id, folder_id)

    new_name = f"{uuid.uuid4().hex}.{(src.ext or 'bin')}"
    try:
        _copy_file(src, new_name)
    except FileNotFoundError:
        raise HTTPException(409, "The original file is missing, so it cannot be copied")

    now = ist.now()
    title = (body.get("title") or "").strip()[:160] or f"{src.title} (copy)"
    dup = Document(
        user_id=user.id, title=title, category=src.category,
        doc_number=src.doc_number, issue_date=src.issue_date,
        expiry_date=src.expiry_date, notes=src.notes,
        filename=new_name, orig_name=src.orig_name, mime=src.mime, ext=src.ext,
        size_bytes=src.size_bytes, ocr_text=src.ocr_text, ocr_at=src.ocr_at,
        has_thumb=src.has_thumb, is_favorite=0, pages=src.pages,
        folder_id=folder_id, content_hash=None,
        kind=src.kind, kind_confidence=src.kind_confidence,
        kind_source=src.kind_source,
        is_trashed=0, created_at=now, updated_at=now)
    # content_hash is left null on purpose: it is the exact-duplicate key, and
    # a copy the owner deliberately asked for must not be reported back to them
    # as an accidental duplicate.
    db.add(dup); db.commit(); db.refresh(dup)
    audit(db, user.id, "copy", "document", dup.id, {"from": id, "label": title})
    return {"item": _present(dup)}


@router.get("/recent")
def recent(limit: int = 30, user: User = Depends(guard("documents", "view")),
           db: Session = Depends(get_db)):
    """Recently added and recently changed, as two lists.

    Kept apart rather than merged into one "recent" feed. They answer
    different questions — "what did I just put in here" and "what did I just
    work on" — and a single list sorted by whichever timestamp is larger
    answers neither reliably.
    """
    base = db.query(Document).filter(Document.user_id == user.id,
                                     Document.is_trashed == 0)
    limit = min(max(1, limit), 100)
    added = base.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit).all()
    changed = (base.filter(Document.updated_at.isnot(None))
               .order_by(Document.updated_at.desc(), Document.id.desc())
               .limit(limit).all())
    starred = (base.filter(Document.is_favorite == 1)
               .order_by(Document.updated_at.desc(), Document.id.desc())
               .limit(limit).all())
    return {"added": [_present(d) for d in added],
            "changed": [_present(d) for d in changed],
            "starred": [_present(d) for d in starred]}


@router.get("/{id}/versions")
def versions(id: int, user: User = Depends(guard("documents", "view")),
             db: Session = Depends(get_db)):
    """Previous copies of this document, newest first."""
    _owned(db, user.id, id)
    rows = (db.query(DocumentVersion)
            .filter(DocumentVersion.user_id == user.id,
                    DocumentVersion.document_id == id)
            .order_by(DocumentVersion.version.desc()).all())
    return {"items": [{"id": v.id, "version": v.version,
                       "orig_name": v.orig_name, "ext": v.ext,
                       "size_bytes": int(v.size_bytes or 0),
                       "note": v.note,
                       "created_at": v.created_at.isoformat() if v.created_at else None}
                      for v in rows],
            "total": len(rows)}


@router.post("/{id}/replace")
def replace(id: int, file: UploadFile = File(...),
            note: str = Form(""),
            user: User = Depends(guard("documents", "edit")),
            db: Session = Depends(get_db)):
    """Put a new file in this document's place, keeping the old one.

    Replacing used to overwrite, so a wrong scan uploaded over a right one
    destroyed the right one. That is the most expensive mistake a document
    store can allow, because what it was holding is usually irreplaceable.

    The outgoing file becomes a version. It is not copied to make the version
    — the row simply takes ownership of the filename that is already on disk,
    and the incoming file gets a new one. Nothing is rewritten and nothing can
    be half-written.
    """
    d = _owned(db, user.id, id)
    raw = file.file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    # MAX_BYTES > 0 first: document_max_mb is 0 on an installation with no
    # limit, and without this guard "unlimited" means "reject everything".
    # Every other size check in this file has the guard; this one did not, and
    # a 45-byte test PDF came back 413.
    if MAX_BYTES > 0 and len(raw) > MAX_BYTES:
        raise HTTPException(413, "That file is too large")

    ext = (os.path.splitext(file.filename or "")[1] or "").lstrip(".").lower()[:10]
    new_name = f"{uuid.uuid4().hex}.{ext or 'bin'}"
    storage.save(storage.DOCUMENTS, user.id, storage.ORIGINAL, new_name, raw)

    now = ist.now()
    nxt = (db.query(func.coalesce(func.max(DocumentVersion.version), 0))
           .filter(DocumentVersion.document_id == id).scalar() or 0) + 1
    db.add(DocumentVersion(
        user_id=user.id, document_id=id, version=int(nxt),
        filename=d.filename, orig_name=d.orig_name, mime=d.mime, ext=d.ext,
        size_bytes=d.size_bytes, content_hash=d.content_hash,
        note=(note or "").strip()[:200] or None, created_at=now))

    d.filename = new_name
    d.orig_name = (file.filename or "")[-255:] or d.orig_name
    d.ext = ext or d.ext
    d.mime = file.content_type or d.mime
    d.size_bytes = len(raw)
    d.content_hash = hashlib.sha256(raw).hexdigest()
    # The text and the type describe the OLD file. Clearing them puts this
    # document back in the indexing queue rather than leaving it searchable by
    # contents it no longer has.
    d.ocr_text = None
    d.ocr_at = None
    if (d.kind_source or "auto") != "user":
        d.kind = None
        d.kind_confidence = None
        d.kind_source = None
    d.updated_at = now
    db.commit()
    audit(db, user.id, "replace", "document", id,
          {"label": d.title, "version": int(nxt)})
    return {"item": _present(d), "kept_as_version": int(nxt)}


@router.post("/{id}/versions/{version}/restore")
def restore_version(id: int, version: int,
                    user: User = Depends(guard("documents", "edit")),
                    db: Session = Depends(get_db)):
    """Make an old version the current file again.

    A swap, not an overwrite: the file being replaced becomes a version of its
    own, so restoring is itself undoable. A restore that discarded the current
    file would be the same trap as the overwrite this feature exists to close.
    """
    d = _owned(db, user.id, id)
    v = (db.query(DocumentVersion)
         .filter(DocumentVersion.user_id == user.id,
                 DocumentVersion.document_id == id,
                 DocumentVersion.version == version).first())
    if not v:
        raise HTTPException(404, "No such version")

    now = ist.now()
    nxt = (db.query(func.coalesce(func.max(DocumentVersion.version), 0))
           .filter(DocumentVersion.document_id == id).scalar() or 0) + 1
    db.add(DocumentVersion(
        user_id=user.id, document_id=id, version=int(nxt),
        filename=d.filename, orig_name=d.orig_name, mime=d.mime, ext=d.ext,
        size_bytes=d.size_bytes, content_hash=d.content_hash,
        note=f"replaced by restoring v{version}", created_at=now))

    d.filename, d.orig_name = v.filename, v.orig_name
    d.mime, d.ext = v.mime, v.ext
    d.size_bytes, d.content_hash = v.size_bytes, v.content_hash
    d.ocr_text = None
    d.ocr_at = None
    d.updated_at = now
    db.delete(v)
    db.commit()
    audit(db, user.id, "restore_version", "document", id,
          {"label": d.title, "version": version})
    return {"item": _present(d), "restored": version, "previous_kept_as": int(nxt)}


@router.post("/{id}/favourite")
def favourite(id: int, user: User = Depends(guard("documents", "edit")), db: Session = Depends(get_db)):
    d = _owned(db, user.id, id)
    d.is_favorite = 0 if d.is_favorite else 1
    db.commit()
    return {"id": id, "is_favourite": int(d.is_favorite)}


def _delete_files(d: Document) -> None:
    storage.remove(storage.DOCUMENTS, d.user_id, storage.ORIGINAL, d.filename)
    storage.remove(storage.DOCUMENTS, d.user_id, storage.THUMB, f"{d.filename}.jpg")


#: How long a binned document is kept. The same 30 days the gallery uses, on
#: purpose: two bins in one product that empty on different schedules is a
#: thing nobody can hold in their head, and the one they guess wrong about is
#: the one holding something irreplaceable.
TRASH_RETENTION_DAYS = 30


def sweep_trash(db: Session, days: int = TRASH_RETENTION_DAYS) -> int:
    """Permanently delete documents binned longer than `days`, and their files.

    Documents have had a trashed_at column since July 2026 and nothing has ever
    read it, so the bin grew forever. Rows binned before this ran get STAMPED
    and not deleted on the same pass, so shipping it does not empty an existing
    bin on the next restart.

    Folders whose whole subtree is gone are removed too, otherwise the tree
    fills with empty binned folders nobody can see or clear.
    """
    now = ist.now()
    unstamped = (db.query(Document)
                 .filter(Document.is_trashed == 1, Document.trashed_at.is_(None)).all())
    if unstamped:
        for d in unstamped:
            d.trashed_at = now
        db.commit()
        print("[sweep] started the retention clock on %d document(s) already in the bin"
              % len(unstamped))

    cutoff = now - timedelta(days=days)
    due = (db.query(Document)
           .filter(Document.is_trashed == 1, Document.trashed_at.isnot(None),
                   Document.trashed_at < cutoff).all())
    n = 0
    for d in due:
        # Files first, then the row, one document at a time. A failure part way
        # through leaves the remainder still in the bin rather than a half
        # committed batch, and each one is independent of the others.
        try:
            _delete_files(d)
        except Exception:
            pass
        db.delete(d)
        n += 1
    folders = (db.query(DocumentFolder)
               .filter(DocumentFolder.is_trashed == 1,
                       DocumentFolder.trashed_at.isnot(None),
                       DocumentFolder.trashed_at < cutoff).all())
    for f in folders:
        db.delete(f)
    if n or folders:
        db.commit()
        print("[sweep] permanently deleted %d document(s) and %d folder(s) binned before %s"
              % (n, len(folders), cutoff.date()))
    return n


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
