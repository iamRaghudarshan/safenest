"""Can a PDF's contents be found when the filename says nothing?

This is the brief's own example, section 32: a file called document_458.pdf
containing "Invoice Number: INV-2026-00123" must be findable by searching for
that number. Before this it was not findable at all — index_ocr_doc skipped
every PDF, stamped it as read, and moved on.

The fixture is a hand-built PDF rather than a generated one, so the test needs
no PDF writer. It is a real PDF with a real text layer; pypdf reads it exactly
as it would read an invoice from a bank.
"""
import os
import tempfile

from app import ocr


def make_pdf(path: str, lines: list[str]) -> None:
    """Write a minimal, valid, single-page PDF with a real text layer."""
    def esc(t):
        return t.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')

    body = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        if i:
            body.append("0 -18 Td")
        body.append(f"({esc(line)}) Tj")
    body.append("ET")
    stream = "\n".join(body).encode("latin-1")

    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>\nstream\n" + stream
        + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<</Size " + str(len(objs) + 1).encode()
            + b"/Root 1 0 R>>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n")
    with open(path, "wb") as f:
        f.write(bytes(out))


tmp = tempfile.mkdtemp(prefix="sn-pdf-")
FAIL = []


def check(ok, label, extra=""):
    print("  %-56s %s %s" % (label, "PASS" if ok else "FAIL", extra))
    if not ok:
        FAIL.append(label)


print("  pdf reader available:", ocr.pdf_available())
check(ocr.pdf_available(), "a PDF can be read without the OCR engine")
check(not ocr.available(),
      "...and the OCR engine is genuinely absent here, so that mattered",
      "(ocr.available()=%s)" % ocr.available())

# --- the brief's example ---------------------------------------------------
print()
print("  --- section 32: findable by contents, not filename ---")
p = os.path.join(tmp, "document_458.pdf")
make_pdf(p, [
    "ACME POWER LIMITED",
    "Invoice Number: INV-2026-00123",
    "GST: 29ABCDE1234F1Z5",
    "Total Due: Rs 4,820.00",
])
text = ocr.read_pdf(p)
check(bool(text), "text came out of the PDF", repr(text[:40]))
check("INV-2026-00123" in text, "the invoice number is in the extracted text")
check("29ABCDE1234F1Z5" in text, "the GST number is in the extracted text")
check("ACME POWER LIMITED" in text, "the supplier name is in the extracted text")
check("document_458" not in text, "the filename is NOT what was matched")

# --- multi-page, and the page cap -----------------------------------------
print()
print("  --- pages ---")
many = os.path.join(tmp, "long.pdf")
make_pdf(many, ["Reference %d" % i for i in range(1, 40)])
t = ocr.read_pdf(many)
check("Reference 1" in t, "a multi-line document reads")

# --- the ways a PDF goes wrong --------------------------------------------
print()
print("  --- malformed input must never raise ---")
bad = os.path.join(tmp, "not-really.pdf")
with open(bad, "wb") as f:
    f.write(b"this is not a pdf at all")
check(ocr.read_pdf(bad) == "", "a file that is not a PDF returns empty, not an error")

empty = os.path.join(tmp, "empty.pdf")
open(empty, "wb").close()
check(ocr.read_pdf(empty) == "", "an empty file returns empty")

check(ocr.read_pdf(os.path.join(tmp, "nope.pdf")) == "",
      "a missing file returns empty")

truncated = os.path.join(tmp, "cut.pdf")
with open(p, "rb") as src, open(truncated, "wb") as dst:
    dst.write(src.read()[: 120])
check(isinstance(ocr.read_pdf(truncated), str),
      "a truncated PDF returns a string rather than raising")

# --- a scanned PDF: honest about the limit --------------------------------
print()
print("  --- the limit this does NOT cover ---")
scanned = os.path.join(tmp, "scan.pdf")
make_pdf(scanned, [])          # a page with no text layer, like a scan
check(ocr.read_pdf(scanned) == "",
      "a page with no text layer yields nothing, as documented")

print()
if FAIL:
    print("  %d FAILED:" % len(FAIL))
    for f in FAIL:
        print("    -", f)
    raise SystemExit(1)
print("  ALL PASS")
