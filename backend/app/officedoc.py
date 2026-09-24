"""Read what is inside a .docx, .xlsx or .pptx — with no converter installed.

WHY THIS EXISTS RATHER THAN LIBREOFFICE. The obvious way to preview an Office
file is to render it, which means shipping LibreOffice or calling a conversion
service. The first adds hundreds of megabytes to a build that currently ships
about 450, on a machine somebody keeps in their house; the second sends the
household's paperwork to a third party, which is the one thing this product
promises never to do.

So this does not render anything. It reads the CONTENT — paragraphs, cells,
slide text — because the question somebody actually has in front of a file
list is "which letter is this?", and the first twenty lines answer it. What it
cannot show is layout, images and formatting, and the screen says so rather
than pretending the plain text is the document.

HOW IT WORKS. Every modern Office file is a ZIP of XML. `zipfile` and
`xml.etree` are both standard library, so the whole feature costs nothing to
install and cannot fail because a binary is missing.

WHAT IT REFUSES. A zip bomb is a real file shape, not a hypothetical: a few
kilobytes can expand to gigabytes, and a preview that decompresses whatever it
is given hands anyone who can upload a document a way to exhaust the machine.
Every part is size-checked before it is read.
"""
from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

#: Namespaces, by the prefix each format uses. Written out rather than read
#: from the file because they have been stable since 2007 and a missing
#: namespace declaration should fail the parse, not silently match nothing.
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

EXT = {"docx": "doc", "xlsx": "sheet", "pptx": "slides"}

#: Caps. Each is the point past which more content stops answering "which
#: file is this?" and starts being a way to spend the machine's memory.
MAX_PART_BYTES = 12 * 1024 * 1024     # one XML part, decompressed
MAX_TOTAL_BYTES = 40 * 1024 * 1024    # all parts of one file
MAX_PARAGRAPHS = 400
MAX_ROWS = 200
MAX_COLS = 30
MAX_SLIDES = 60
MAX_CELL_CHARS = 200


class OfficeError(ValueError):
    """A file that cannot be read, with a sentence for the person."""


def _read_part(z: zipfile.ZipFile, name: str, budget: list[int]) -> bytes | None:
    """One part, refused if it is too big to be worth trusting."""
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES or info.file_size > budget[0]:
        raise OfficeError("That file is too large to preview")
    budget[0] -= info.file_size
    with z.open(info) as fh:
        return fh.read(MAX_PART_BYTES + 1)


def _text_of(node, tag: str) -> str:
    """All text under a node, in document order."""
    return "".join(t.text or "" for t in node.iter(tag))


def _docx(z: zipfile.ZipFile, budget: list[int]) -> dict:
    raw = _read_part(z, "word/document.xml", budget)
    if raw is None:
        raise OfficeError("That document has no readable text part")
    root = ET.fromstring(raw)
    paras: list[str] = []
    truncated = False
    for p in root.iter(W + "p"):
        if len(paras) >= MAX_PARAGRAPHS:
            truncated = True
            break
        # w:t holds the text; a paragraph is often several runs, and joining
        # them is what turns "Dear " + "Priya" back into a sentence.
        line = _text_of(p, W + "t").strip()
        # Blank paragraphs are spacing, not content. Keeping every one of them
        # makes a two-page letter scroll like a ten-page one.
        if line or (paras and paras[-1]):
            paras.append(line)
    while paras and not paras[-1]:
        paras.pop()
    return {"kind": "doc", "paragraphs": paras, "truncated": truncated}


def _xlsx(z: zipfile.ZipFile, budget: list[int]) -> dict:
    # Shared strings are the trap: a cell of type "s" holds an INDEX into this
    # table, not the text. Reading the sheet alone gives a grid of integers
    # that looks like data and is not.
    shared: list[str] = []
    raw = _read_part(z, "xl/sharedStrings.xml", budget)
    if raw:
        for si in ET.fromstring(raw).iter(S + "si"):
            shared.append(_text_of(si, S + "t")[:MAX_CELL_CHARS])

    sheet = None
    for name in z.namelist():
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
            sheet = name
            break
    if sheet is None:
        raise OfficeError("That workbook has no sheets")
    raw = _read_part(z, sheet, budget)
    if raw is None:
        raise OfficeError("That workbook could not be read")

    root = ET.fromstring(raw)
    rows: list[list[str]] = []
    truncated = False
    for r in root.iter(S + "row"):
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        cells: list[str] = []
        for cxml in r.iter(S + "c"):
            if len(cells) >= MAX_COLS:
                truncated = True
                break
            v = cxml.find(S + "v")
            text = ""
            if cxml.get("t") == "s":
                try:
                    text = shared[int(v.text)] if v is not None else ""
                except (ValueError, IndexError):
                    text = ""
            elif cxml.get("t") == "inlineStr":
                text = _text_of(cxml, S + "t")
            elif v is not None:
                text = v.text or ""
            cells.append(text[:MAX_CELL_CHARS])
        # A row of nothing is a formatting artefact; a spreadsheet full of
        # them reads as a broken preview.
        if any(c.strip() for c in cells):
            rows.append(cells)
    return {"kind": "sheet", "rows": rows, "truncated": truncated}


def _pptx(z: zipfile.ZipFile, budget: list[int]) -> dict:
    names = sorted(
        (n for n in z.namelist()
         if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
        # slide10 must not sort before slide2, which is what a plain sort does
        # and what makes a deck's preview read in the wrong order.
        key=lambda n: int("".join(ch for ch in n.rsplit("/", 1)[-1]
                                  if ch.isdigit()) or 0))
    slides: list[list[str]] = []
    truncated = len(names) > MAX_SLIDES
    for name in names[:MAX_SLIDES]:
        raw = _read_part(z, name, budget)
        if raw is None:
            continue
        root = ET.fromstring(raw)
        lines = [t.strip() for t in
                 ("".join(x.text or "" for x in p.iter(A + "t"))
                  for p in root.iter(A + "p"))
                 if t.strip()]
        slides.append(lines)
    if not slides:
        raise OfficeError("That presentation has no readable slides")
    return {"kind": "slides", "slides": slides, "truncated": truncated}


def read(path: str, ext: str) -> dict:
    """Content of one Office file. Raises OfficeError with a readable reason."""
    ext = (ext or "").lower()
    if ext not in EXT:
        raise OfficeError("That is not an Office file this can read")
    budget = [MAX_TOTAL_BYTES]
    try:
        with zipfile.ZipFile(path) as z:
            if ext == "docx":
                out = _docx(z, budget)
            elif ext == "xlsx":
                out = _xlsx(z, budget)
            else:
                out = _pptx(z, budget)
    except OfficeError:
        raise
    except zipfile.BadZipFile:
        # The common cause is a genuine .doc/.xls — the pre-2007 binary
        # formats, which are not zips at all and which this cannot read.
        raise OfficeError("That file is not in the modern Office format")
    except ET.ParseError:
        raise OfficeError("That file's contents could not be read")
    out["text_only"] = True
    return out
