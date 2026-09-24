# Documents → Google Drive parity

Every row checked against the code, not remembered. **DONE** means the
endpoint exists *and* a test or a click proves it; **API only** means it works
but nothing in the app calls it, which from a user's seat is the same as
missing.

Last audited 24 September 2026, after the multi-select / filters / preview
batch. Everything in sections A and B below is now DONE and tested; what
remains is section C, which was always "decide before starting".

---

## 1. Files and folders

| Drive behaviour | Status | Notes |
|---|---|---|
| Upload a file | DONE | single-shot and chunked |
| Folders, subfolders | DONE | adjacency list; move into own descendant refused |
| Breadcrumb navigation | DONE | bounded walk, so a cycle cannot hang a request |
| Move a file | DONE | `POST /api/documents/move` |
| Rename | DONE | |
| Copy | DONE | button in the viewer |
| Delete → Trash → Restore | DONE | 30-day retention sweep |
| Star / favourite | DONE | |
| Move a folder into another folder | DONE | via the move sheet |
| Rename a folder | DONE | opens with the CURRENT name, not an empty box |
| Drag and drop into a folder | DONE | moves the whole selection if the dragged file is in it |
| Multi-select files | DONE | once anything is picked, a tap selects rather than opens |
| Bulk move / delete / star | DONE | scoped by `user_id` in the filter, not by id alone |

## 2. Finding things

| Drive behaviour | Status | Notes |
|---|---|---|
| Search filename | DONE | |
| Search inside PDFs | DONE | text layer; scanned PDFs still not covered |
| Search inside images | DONE | OCR, where the engine is installed |
| Search by category | DONE | the existing category chips |
| Recent | DONE | a chip beside the categories; it cuts across them |
| Filter by type and date | DONE | grouped by what a file IS, not by extension |
| Filter by owner / size | Not applicable | one household, one owner; size is a sort |
| Sort by name / modified / size | DONE | in the filter panel |
| Automatic classification | DONE | correction sheet shows the classifier's evidence |

## 3. Versions

| Drive behaviour | Status | Notes |
|---|---|---|
| Replace keeps the old file | DONE | tested — nothing is destroyed |
| List versions | DONE | panel in the viewer |
| Restore a version | DONE | restore is itself undoable |
| Download a specific version | DONE | so you can check which one you want first |
| Version retention limit | DONE | last ten, oldest dropped, and the sheet says so |
| Versions die with the document | DONE | they did not: ids get reused, and a new document inherited a deleted one's history |
| Name / annotate a version | DONE | `note` on replace |

## 4. Preview

| Drive behaviour | Status | Notes |
|---|---|---|
| Image preview | DONE | |
| PDF preview | DONE | |
| Text / CSV preview | DONE | read on the server: 256KB, 200 rows, and it says what it cut |
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

Sharing was decided: **export to a file** the owner sends themselves, not a
hosted link. Built — select files, then the ⤓ button. A link would be a door
into somebody's paperwork that stays open as long as the link exists, which is
the thing they installed this instead of.

---

## Ordered task list

**A — makes what exists reachable.** All done.
1. ~~Versions panel: list, restore, and the note~~
2. ~~Recent screen: added / changed / starred~~
3. ~~Copy, from the document menu~~
4. ~~Document type correction, with the classifier's evidence shown~~
5. ~~Sort control: name / modified / size~~ — in the filter panel. An
   explicit order turns the favourites float off, which is tested, because
   otherwise "by name" quietly means "starred, then by name".
6. ~~Rename and move a folder~~

**B — genuinely missing.** All done.
7. ~~Multi-select in documents, then bulk move / delete / star~~
8. ~~Download a specific version~~
9. ~~Version retention~~ — ten, oldest dropped
10. ~~Text and CSV preview~~
11. ~~Filter by type and date~~
12. ~~Drag and drop onto a folder~~

**C — large, decide before starting.** Unchanged; none started.
13. Office preview — needs LibreOffice or a conversion service; adds hundreds
    of megabytes to a build that currently ships ~450 MB. Recommendation:
    don't. The download card is a worse experience than a preview, and a much
    better one than a 700MB install.
14. Video and audio preview inside documents
15. ~~Any form of sharing~~ — decided and built as export-to-zip

---

## Measured today

Tested and passing: folders (13 checks), copy/recent/versions (22), PDF
content search (13), classification (6 plus precision and recall on 14 types
and 5 non-documents), trash retention (shared with the gallery), bulk /
export / retention / saved searches (31), type and date filters (20), text and
CSV preview (28), version cascade on delete (6).

Plus 24 in a real browser against the throwaway instance — the selection bar,
the second tap selecting rather than opening, rename opening with the current
name, a folder lighting up under a drag and the drop actually moving the file,
and a CSV arriving as a table with its quoted commas intact.

Untested because they cannot be tested here: nothing in this module — every
item above is reachable from a throwaway instance on this machine.

**Running the suite:** `python verify_all.py`. Not a `for` loop — the login
limiter allows ten attempts per five minutes, and twenty-five scripts each
signing in walk straight into it. The runner treats a 429 as "wait", not as a
failure, because a suite that cries wolf gets ignored.
