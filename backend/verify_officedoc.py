"""Reading .docx, .xlsx and .pptx without a converter installed.

Four traps, each of which produces a preview that LOOKS fine and is wrong:

  * a docx paragraph is often several runs — "Dear " and "Priya" — because
    somebody edited a word in the middle. Reading runs separately gives a
    document broken into fragments.
  * an xlsx cell of type "s" holds an INDEX into the shared-string table, not
    the text. Ignoring that table gives a grid of small integers which looks
    like data and is not.
  * slide10 sorts before slide2 as a string, so a deck reads out of order.
  * a zip a few kilobytes long can expand to gigabytes. A preview that
    decompresses whatever it is handed gives anyone who can upload a file a
    way to exhaust the machine.

The fixtures are written here, by hand, from the OOXML shapes — not generated
by a library that shares the reader's assumptions, because that would prove
only that the assumptions agree with themselves.

Pure: no server, no database.
"""
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import officedoc  # noqa: E402

FAIL = []
TMP = tempfile.mkdtemp(prefix="office-")


def check(ok, label, extra=""):
    line = "  %-58s %s %s" % (label, "PASS" if ok else "FAIL", extra)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(enc, "replace").decode(enc, "replace"))
    if not ok:
        FAIL.append(label)


CT = ('<?xml version="1.0" encoding="UTF-8"?><Types '
      'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def build(name, parts):
    path = os.path.join(TMP, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        for k, v in parts.items():
            z.writestr(k, v)
    return path


print("\n  --- docx ---")
body = "".join('<w:p><w:r><w:t>%s</w:t></w:r></w:p>' % t for t in (
    "Rental agreement", "The rent is 18,000 rupees per month."))
# THE TRAP: one paragraph, two runs.
body += ('<w:p><w:r><w:t>Signed on </w:t></w:r>'
         '<w:r><w:t>1 April 2026.</w:t></w:r></w:p>')
body += "<w:p/>" * 3          # spacer paragraphs
doc = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body))
got = officedoc.read(build("a.docx", {"word/document.xml": doc}), "docx")
check(got["kind"] == "doc", "a .docx reads as a document", got["kind"])
check(got["paragraphs"][0] == "Rental agreement", "the first paragraph is its title",
      got["paragraphs"][:1])
check("Signed on 1 April 2026." in got["paragraphs"],
      "runs inside one paragraph are joined back into a sentence",
      [p for p in got["paragraphs"] if "Signed" in p])
check(got["paragraphs"][-1] != "", "trailing blank paragraphs are dropped",
      got["paragraphs"][-1])
check(got.get("text_only") is True, "and it declares itself text only",
      got.get("text_only"))


print("\n  --- xlsx ---")
shared = ('<?xml version="1.0" encoding="UTF-8"?><sst xmlns="%s">'
          '<si><t>Item</t></si><si><t>Amount</t></si>'
          '<si><t>Rent</t></si></sst>' % S)
sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="%s"><sheetData>'
         '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
         '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>18000</v></c></row>'
         '<row r="3"/>'
         '<row r="4"><c r="A4" t="inlineStr"><is><t>Inline</t></is></c></row>'
         '</sheetData></worksheet>' % S)
got = officedoc.read(build("a.xlsx", {"xl/sharedStrings.xml": shared,
                                      "xl/worksheets/sheet1.xml": sheet}), "xlsx")
check(got["kind"] == "sheet", "an .xlsx reads as rows", got["kind"])
# THE ONE THAT MATTERS: without the shared-string table this row is ['0','1'].
check(got["rows"][0] == ["Item", "Amount"],
      "shared strings are resolved, not shown as their indexes", got["rows"][0])
check(got["rows"][1] == ["Rent", "18000"], "numbers come through as themselves",
      got["rows"][1])
check(["Inline"] in got["rows"], "an inline string is read too", got["rows"])
check(all(any(c.strip() for c in r) for r in got["rows"]),
      "empty rows are dropped rather than shown as blank lines", got["rows"])


print("\n  --- pptx ---")


def slide(*lines):
    paras = "".join('<a:p><a:r><a:t>%s</a:t></a:r></a:p>' % t for t in lines)
    return ('<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="%s" xmlns:a="%s">'
            '<p:cSld><p:spTree><p:sp><p:txBody>%s</p:txBody></p:sp>'
            '</p:spTree></p:cSld></p:sld>' % (P, A, paras))


got = officedoc.read(build("a.pptx", {
    "ppt/slides/slide1.xml": slide("Household budget", "April 2026"),
    "ppt/slides/slide2.xml": slide("Where it goes"),
    "ppt/slides/slide10.xml": slide("The last slide"),
}), "pptx")
check(got["kind"] == "slides", "a .pptx reads as slides", got["kind"])
check(len(got["slides"]) == 3, "every slide is there", len(got["slides"]))
# THE TRAP: a plain string sort puts slide10 between slide1 and slide2.
check(got["slides"][-1] == ["The last slide"],
      "slide 10 comes after slide 2, not after slide 1", got["slides"])
check(got["slides"][0] == ["Household budget", "April 2026"],
      "a slide's lines are in order", got["slides"][0])


print("\n  --- what it refuses ---")
for ext, why in (("doc", "a pre-2007 .doc"), ("pdf", "a PDF"), ("", "no extension")):
    try:
        officedoc.read(build("x.bin", {}), ext)
        check(False, why + " is refused")
    except officedoc.OfficeError as e:
        check(True, why + " is refused", str(e)[:40])

# A real .doc is not a zip at all, so the message has to be about the format
# rather than "could not be read".
path = os.path.join(TMP, "old.docx")
with open(path, "wb") as fh:
    fh.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)  # OLE2 header
try:
    officedoc.read(path, "docx")
    check(False, "a binary .doc renamed .docx is refused")
except officedoc.OfficeError as e:
    check("modern Office format" in str(e),
          "a binary .doc renamed .docx says so plainly", str(e))

path = build("empty.xlsx", {"xl/sharedStrings.xml": shared})
try:
    officedoc.read(path, "xlsx")
    check(False, "a workbook with no sheets is refused")
except officedoc.OfficeError as e:
    check(True, "a workbook with no sheets is refused", str(e)[:40])

path = build("broken.docx", {"word/document.xml": "<w:document><not closed"})
try:
    officedoc.read(path, "docx")
    check(False, "malformed XML is refused")
except officedoc.OfficeError as e:
    check(True, "malformed XML is refused, not raised raw", str(e)[:40])


print("\n  --- the zip bomb ---")
# 60MB of zeroes compresses to a few kilobytes. Reading it would be the whole
# preview feature handing over the machine's memory.
bomb = os.path.join(TMP, "bomb.docx")
with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", CT)
    z.writestr("word/document.xml", b"\0" * (60 * 1024 * 1024))
size = os.path.getsize(bomb)
check(size < 200_000, "the bomb really is small on disk", "%d bytes" % size)
try:
    officedoc.read(bomb, "docx")
    check(False, "a part that expands to 60MB is refused")
except officedoc.OfficeError as e:
    check("too large" in str(e), "a part that expands to 60MB is refused", str(e))

print()
if FAIL:
    print("  %d FAILED:" % len(FAIL))
    for f in FAIL:
        print("    -", f)
    raise SystemExit(1)
print("  ALL PASS")
