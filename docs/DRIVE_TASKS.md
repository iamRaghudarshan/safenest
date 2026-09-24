# Documents → Google Drive parity

Every row checked against the code, not remembered. **DONE** means the
endpoint exists *and* a test or a click proves it; **API only** means it works
but nothing in the app calls it, which from a user's seat is the same as
missing.

Last audited 24 September 2026.

---

## 1. Files and folders

| Drive behaviour | Status | Notes |
|---|---|---|
| Upload a file | DONE | single-shot and chunked |
| Folders, subfolders | DONE | adjacency list; move into own descendant refused |
| Breadcrumb navigation | DONE | bounded walk, so a cycle cannot hang a request |
| Move a file | DONE | `POST /api/documents/move` |
| Rename | DONE | |
| Copy | API only | real byte copy; **no button** |
| Delete → Trash → Restore | DONE | 30-day retention sweep |
| Star / favourite | DONE | |
| **Move a folder into another folder** | **TODO** | `PUT /folders/{id}` accepts `parent_id`; no UI |
| **Rename a folder** | **TODO** | endpoint accepts `name`; no UI |
| **Drag and drop into a folder** | **TODO** | |
| **Multi-select files** | **TODO** | the gallery has it; documents do not |
| **Bulk move / delete / star** | **TODO** | needs multi-select first |

## 2. Finding things

| Drive behaviour | Status | Notes |
|---|---|---|
| Search filename | DONE | |
| Search inside PDFs | DONE | text layer; scanned PDFs still not covered |
| Search inside images | DONE | OCR, where the engine is installed |
| Search by category | DONE | the existing category chips |
| **Recent** | **API only** | added / changed / starred returned; no screen |
| **Filter by type, owner, date, size** | **TODO** | `sort` exists; no filter UI |
| **Sort by name / modified / size** | **API only** | `?sort=` works; no control |
| **Automatic classification** | **API only** | 14 types, abstains, explains; no correction UI |

## 3. Versions

| Drive behaviour | Status | Notes |
|---|---|---|
| Replace keeps the old file | DONE | tested — nothing is destroyed |
| List versions | API only | no panel |
| Restore a version | API only | restore is itself undoable |
| Download a specific version | **TODO** | no endpoint serves version bytes |
| **Version retention limit** | **TODO** | versions accumulate forever today |
| Name / annotate a version | DONE | `note` on replace |

## 4. Preview

| Drive behaviour | Status | Notes |
|---|---|---|
| Image preview | DONE | |
| PDF preview | DONE | |
| Text / CSV preview | **TODO** | |
| **Office (docx, xlsx, pptx)** | **TODO** | needs a converter; large dependency |
| Video / audio preview | **TODO** | the gallery plays video; documents do not |

## 5. Not being built as specified

| Drive behaviour | Why |
|---|---|
| Share with named people | No accounts beyond the household |
| Public / link sharing | The product's promise is *"never to us"*; a public link is the door it exists to keep shut |
| Comments, suggestions | Requires multiple users |
| Real-time collaboration | Requires a server everyone reaches |
| "Shared with me" | Nothing to share from |

If sharing is genuinely wanted, the honest shape is **export to a file** the
owner sends themselves — not a hosted link. That needs a decision before it
is built.

---

## Ordered task list

**A — makes what exists reachable** (all backend work is done; this is UI)
1. Versions panel: list, restore, and the note
2. Recent screen: added / changed / starred
3. Copy, from the document menu
4. Document type correction, with the classifier's evidence shown
5. Sort control: name / modified / size
6. Rename and move a folder

**B — genuinely missing**
7. Multi-select in documents, then bulk move / delete / star
8. Download a specific version (endpoint + button)
9. Version retention: cap the count or the age, and say which
10. Text and CSV preview
11. Filter by type and date
12. Drag and drop onto a folder

**C — large, decide before starting**
13. Office preview — needs LibreOffice or a conversion service; adds hundreds
    of megabytes to a build that currently ships ~450 MB
14. Video and audio preview inside documents
15. Any form of sharing — needs the decision above

---

## Measured today

Tested and passing: folders (13 checks), copy/recent/versions (22), PDF
content search (13), classification (6 plus precision and recall on 14 types
and 5 non-documents), trash retention (shared with the gallery).

Untested because they cannot be tested here: nothing in this module — every
item above is reachable from a throwaway instance on this machine.
