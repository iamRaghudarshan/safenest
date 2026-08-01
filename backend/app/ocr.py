"""Reading the text inside documents and photos, on this machine.

A scanned bill is a picture until something reads it. Until now FinMate stored
those pictures and asked the owner to retype every field into the form beside
them — the due date, the amount, the policy number — while the answer was sitting
in the image the whole time.

Runs entirely on the device through the same onnxruntime the face and CLIP models
already use. Nothing is uploaded, which matters more here than anywhere else in
the app: these are passports, bank statements and medical records.

WHAT IT IS NOT
OCR is a good reader, not a perfect one. Every value it pulls out is offered as a
suggestion for one tap, never written silently. A wrong expiry date that appeared
by itself is worse than no expiry date at all, because nobody thinks to check it.
"""
from __future__ import annotations

import io
import re
import threading
from datetime import date, datetime

import numpy as np

_lock = threading.RLock()
_engine = None
_failed = False

MAX_SIDE = 1600          # bigger costs time and finds nothing extra at document DPI
MIN_SCORE = 0.50         # below this the reading is usually noise


def available() -> bool:
    """Whether text extraction can run at all."""
    global _failed
    if _engine is not None:
        return True
    if _failed:
        return False
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:
        _failed = True
        return False


def _get():
    """The OCR engine, built once.

    Loading costs about a second and a half and holds three ONNX sessions, so it
    is built on first use and kept. RLock rather than Lock: the same thread can
    re-enter through read_image while already holding it.
    """
    global _engine, _failed
    if _engine is not None:
        return _engine
    # Checked, not merely set. _failed was recorded on failure and then never read
    # here, so every document and every photo rebuilt the engine from scratch,
    # failed the same way, and printed the same line -- a console scrolling past
    # itself every two seconds on a machine where onnxruntime cannot load at all.
    # One attempt, one message, and the app carries on without text search.
    if _failed:
        return None
    with _lock:
        if _engine is not None:
            return _engine
        if _failed:
            return None
        try:
            from rapidocr import RapidOCR
            _engine = RapidOCR()
        except Exception as exc:
            _failed = True
            print(f"[ocr] unavailable, so text inside documents and photos will "
                  f"not be searchable: {exc}")
            if "DLL load failed" in str(exc):
                # Naming the actual remedy. Everything else about this message is
                # unactionable to the person reading it.
                print("[ocr] this usually means the Microsoft Visual C++ "
                      "Redistributable (x64) is missing — install it from "
                      "microsoft.com and restart.")
            print("[ocr] everything else works normally. This is not retried.")
            return None
    return _engine


def _prepare(pil) -> np.ndarray | None:
    """Downscale and flatten to RGB. Returns None if the image is unusable."""
    try:
        if pil.mode in ("RGBA", "LA", "P"):
            pil = pil.convert("RGB")
        elif pil.mode != "RGB":
            pil = pil.convert("RGB")
        w, h = pil.size
        if max(w, h) > MAX_SIDE:
            scale = MAX_SIDE / max(w, h)
            pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        return np.array(pil)
    except Exception:
        return None


def read_image(pil) -> str:
    """All the text in one image, in reading order. Empty string if none."""
    engine = _get()
    if engine is None:
        return ""
    frame = _prepare(pil)
    if frame is None:
        return ""
    try:
        with _lock:
            result = engine(frame)
    except Exception as exc:
        print(f"[ocr] failed on an image: {exc}")
        return ""

    texts = getattr(result, "txts", None) or []
    scores = getattr(result, "scores", None) or []
    lines = []
    for i, text in enumerate(texts):
        score = scores[i] if i < len(scores) else 1.0
        if text and float(score) >= MIN_SCORE:
            lines.append(str(text).strip())
    return "\n".join(l for l in lines if l)


def read_bytes(raw: bytes) -> str:
    """Text from encoded image bytes."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            return read_image(im)
    except Exception:
        return ""


# --------------------------------------------------------------- field pulling
# Indian documents mix these orders freely, so all of them are accepted.
#
# Two-digit years are accepted too, via the usual sliding window. An earlier
# version refused them on the grounds that "05-08-26" is ambiguous — but the
# ambiguity there is day-versus-month, which this code already resolves by
# assuming day-first for four-digit years as well. Only the century was ever in
# doubt. Refusing them meant real receipts, which almost all print "12-07-26",
# produced no date at all.
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-\s]([A-Za-z]{3,9})[-\s,]+(\d{4})\b"), "dMy"),
    (re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})(?!\d)"), "dmy2"),
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Words that mark WHICH date a line is talking about. Without these every
# document hands back its print date as an expiry.
_EXPIRY_WORDS = re.compile(
    r"\b(expir\w*|valid\s*(?:up\s*to|till|until|thru)|valid\s*upto|due\s*date|"
    r"renewal|next\s*due)\b", re.I)
_ISSUE_WORDS = re.compile(
    r"\b(issue\w*|bill\s*date|invoice\s*date|date\s*of\s*issue|dated|from)\b", re.I)
# Receipts abbreviate. Requiring the long form made "Amt" — the single most
# common money label on an Indian receipt — invisible.
_AMOUNT_WORDS = re.compile(
    r"\b(amount|amt|total|payable|grand\s*total|net\s*payable|balance\s*due|"
    r"bal|paid|sub\s*total)\b", re.I)
# Only these justify picking ONE number and calling it "the" amount. A line that
# merely says "Amt" above a column of prices is a list of items, not a total, and
# guessing which one is the total would be wrong more often than right.
_TOTAL_WORDS = re.compile(
    r"\b(grand\s*total|net\s*payable|total|payable|amount\s*(?:payable|due))\b", re.I)

_AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)|"
                     r"([\d,]+\.\d{2})\b", re.I)


def _to_date(match, order: str) -> date | None:
    try:
        a, b, c = match.groups()
        if order == "dmy":
            d, m, y = int(a), int(b), int(c)
        elif order == "dmy2":
            d, m, y = int(a), int(b), int(c)
            y += 2000 if y <= 68 else 1900     # the POSIX sliding window
        elif order == "ymd":
            y, m, d = int(a), int(b), int(c)
        else:
            d, y = int(a), int(c)
            m = _MONTHS.get(b[:3].lower(), 0)
        if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2200):
            return None
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def _dates_in(line: str) -> list[date]:
    found = []
    for pattern, order in _DATE_PATTERNS:
        for match in pattern.finditer(line):
            got = _to_date(match, order)
            if got:
                found.append(got)
    return found


def extract(text: str, today: date | None = None) -> dict:
    """Pull the fields a form would ask for out of raw OCR text.

    Line-by-line rather than over the whole blob: which date is the expiry is
    decided by the words next to it, and that context disappears the moment
    everything is flattened into one string.
    """
    today = today or date.today()
    out: dict = {"expiry_date": None, "issue_date": None, "amount": None,
                 "doc_number": None, "dates": [], "amounts": []}
    if not text:
        return out
    money_seen: list[float] = []

    # OCR emits one line per detected text BOX, not per printed line. On a real
    # receipt "Amt" is its own box with the figures in boxes beneath it, and
    # "Total:Rs" is separated from its number the same way. Matching label and
    # value on a single line works on a synthetic image and finds nothing at all
    # on an actual bill — so a label with no number of its own looks ahead.
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    LOOKAHEAD = 3

    def _numbers_near(index: int, span: int = 8) -> list[float]:
        """Every figure in the boxes at and just below a money label.

        Collected across the whole window rather than stopping at the first line
        that has a number: a receipt prints one figure per box, so stopping early
        returned one item out of six. These become the choices offered, so extra
        candidates cost nothing while a missing one cannot be recovered.
        """
        found: list[float] = []
        for line in lines[index:index + 1 + span]:
            for money in _AMOUNT.finditer(line):
                raw = (money.group(1) or money.group(2) or "").replace(",", "")
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if 0 < value < 1e9:
                    found.append(value)
        return found

    def _dates_near(index: int) -> list[date]:
        for line in lines[index:index + 1 + LOOKAHEAD]:
            got = _dates_in(line)
            if got:
                return got
        return []

    every: list[date] = []
    for position, line in enumerate(lines):
        dates = _dates_in(line)
        every.extend(dates)
        if _EXPIRY_WORDS.search(line) and out["expiry_date"] is None:
            near = dates or _dates_near(position)
            if near:
                out["expiry_date"] = near[0]
        elif _ISSUE_WORDS.search(line) and out["issue_date"] is None:
            near = dates or _dates_near(position)
            if near:
                out["issue_date"] = near[0]

        if _AMOUNT_WORDS.search(line):
            values = _numbers_near(position)
            money_seen.extend(values)
            # Under a "Total" label the figure immediately after it is the total.
            # Under a bare "Amt" heading the figures are line items, so nothing is
            # named the total and the caller is offered the list instead.
            if values and out["amount"] is None and _TOTAL_WORDS.search(line):
                out["amount"] = values[0]

        if out["doc_number"] is None:
            # A labelled identifier: "Consumer No: 4471 8890 2231", "Policy #A/22".
            label = re.search(
                r"\b(?:no|number|no\.|#|id|policy|account|consumer|folio|card)\b"
                r"\s*[:.#-]?\s*([A-Z0-9][A-Z0-9\s/-]{5,24})", line, re.I)
            if label:
                candidate = label.group(1).strip(" -/")
                if sum(ch.isdigit() for ch in candidate) >= 4:
                    out["doc_number"] = re.sub(r"\s{2,}", " ", candidate)

    # Nothing labelled? A future date in a document is far more likely to be an
    # expiry than anything else, so offer it rather than leaving the field blank.
    if out["expiry_date"] is None:
        ahead = sorted(d for d in every if d > today)
        if ahead:
            out["expiry_date"] = ahead[0]
    # Receipts label the transaction date just "Date:", which is too weak a word
    # to trust on its own — it appears on everything. As a fallback though, the
    # most recent past date on a document is nearly always when it was issued.
    if out["issue_date"] is None:
        behind = sorted((d for d in every if d <= today), reverse=True)
        if behind:
            out["issue_date"] = behind[0]
    out["dates"] = sorted(set(every))
    # Largest first: on a receipt the total is almost always the biggest number,
    # so the value someone wants is at the top of the list they are offered.
    out["amounts"] = sorted({round(v, 2) for v in money_seen}, reverse=True)[:8]
    return out


def summarise(text: str, limit: int = 240) -> str:
    """A short, single-line preview for a list row."""
    flat = re.sub(r"\s+", " ", (text or "").strip())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def iso(value) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return None
