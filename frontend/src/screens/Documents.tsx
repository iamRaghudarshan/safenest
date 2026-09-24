import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { DocText } from '../DocText'
import { api, apiBlob, errorMessage, tokenStore } from '../api'
import { useNav, useOverlayBack } from '../nav'
import { useAuth } from '../auth'
import { useToast } from '../toast'
import { TopBar, Spinner, Empty, Sheet, Field } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { formatBytes } from '../maintenance'
import { fmtDate } from '../format'
import { Zoomable } from '../Zoomable'
import { ScanFlow } from './Scan'
import type { DocFolder, DocumentItem, DocumentsData, MasterItem } from '../types'

type Cat = { key: string; label: string; emoji: string }

// Built-in fallback — used until the user's master list loads (or if it fails).
const BUILTIN_CATS: Cat[] = [
  { key: 'id', label: 'ID Cards', emoji: '🪪' },
  { key: 'financial', label: 'Financial', emoji: '💳' },
  { key: 'insurance', label: 'Insurance', emoji: '🛡️' },
  { key: 'vehicle', label: 'Vehicle', emoji: '🚗' },
  { key: 'property', label: 'Property', emoji: '🏠' },
  { key: 'medical', label: 'Medical', emoji: '🏥' },
  { key: 'education', label: 'Education', emoji: '🎓' },
  { key: 'other', label: 'Other', emoji: '📄' },
]

// Categories come from the user's editable master list (Profile → Manage lists).
const CatsCtx = createContext<Cat[]>(BUILTIN_CATS)
const useCats = () => useContext(CatsCtx)

/** The type filter, worded the way people look for a file rather than by
    extension — "was it .xls or .xlsx" is the question this exists to avoid.
    Keys match TYPE_GROUPS in the backend; `other` is everything else. */
/** The orders the server already understood and nothing offered.
 *
 *  "Starred first" is the empty string because it is the DEFAULT, not an
 *  option added later — naming it in the list is the only way somebody can
 *  get back to it after picking another. */
const SORTS = [
  { key: '', label: 'Starred first' },
  { key: 'name', label: 'Name' },
  { key: 'oldest', label: 'Oldest first' },
  { key: 'largest', label: 'Largest' },
  { key: 'smallest', label: 'Smallest' },
]

const FILE_TYPES = [
  { key: 'pdf', label: 'PDFs', emoji: '📕' },
  { key: 'image', label: 'Images', emoji: '🖼️' },
  { key: 'doc', label: 'Documents', emoji: '📝' },
  { key: 'sheet', label: 'Spreadsheets', emoji: '📊' },
  { key: 'slides', label: 'Slides', emoji: '📽️' },
  { key: 'archive', label: 'Archives', emoji: '🗜️' },
  { key: 'other', label: 'Other', emoji: '📎' },
]
const catMeta = (cats: Cat[], k: string): Cat =>
  cats.find((c) => c.key === k) || { key: k, label: k || 'Other', emoji: '📄' }

const mask = (n?: string | null) => {
  const s = (n || '').trim()
  return s.length <= 4 ? s : s.slice(0, -4).replace(/\S/g, '•') + s.slice(-4)
}

// Fetch a private, auth-protected file as an object URL (JWT in the header, never the URL).
function useAuthedBlob(url?: string | null) {
  const [obj, setObj] = useState<string | null>(null)
  useEffect(() => {
    if (!url) { setObj(null); return }
    let alive = true
    let made: string | null = null
    fetch(url, { headers: { Authorization: `Bearer ${tokenStore.get()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('load'))))
      .then((b) => { if (alive) { made = URL.createObjectURL(b); setObj(made) } })
      .catch(() => { if (alive) setObj(null) })
    return () => { alive = false; if (made) URL.revokeObjectURL(made) }
  }, [url])
  return obj
}

function AuthImg({ src, className }: { src?: string | null; className?: string }) {
  const obj = useAuthedBlob(src)
  if (!obj) return <div className={`${className || ''} doc-imgload`}><span className="spinner sm" /></div>
  return <img src={obj} className={className} />
}

/** One icon rule for every place a document is listed. */
function docIcon(d: { is_pdf: boolean; is_image: boolean; ext: string }) {
  if (d.is_pdf) return '📄'
  if (d.is_image) return '🖼️'
  const e = (d.ext || '').toLowerCase()
  if (/^(xls|xlsx|csv|ods)$/.test(e)) return '📊'
  if (/^(doc|docx|odt|rtf|txt)$/.test(e)) return '📝'
  if (/^(zip|rar|7z)$/.test(e)) return '🗜️'
  if (/^(mp3|wav|m4a)$/.test(e)) return '🎵'
  if (/^(mp4|mov|avi|mkv)$/.test(e)) return '🎬'
  return '📎'
}

export default function Documents() {
  const { back, canBack } = useNav()
  const { can } = useAuth()
  const toast = useToast()
  const canEdit = can('documents')
  const fileRef = useRef<HTMLInputElement>(null)

  const [data, setData] = useState<DocumentsData | null>(null)
  const [cats, setCats] = useState<Cat[]>(BUILTIN_CATS)
  const [cat, setCat] = useState('')     // '' = all
  const [q, setQ] = useState('')
  const [addFile, setAddFile] = useState<File | null>(null)
  const [view, setView] = useState<DocumentItem | null>(null)
  const [edit, setEdit] = useState<DocumentItem | null>(null)
  const [scanning, setScanning] = useState(false)
  const [trashOpen, setTrashOpen] = useState(false)
  // Which folder is open. 0 is the top level, which is a real place — not
  // "no filter". Searching leaves the tree entirely (see `load`).
  const [folderId, setFolderId] = useState(0)
  const [newFolder, setNewFolder] = useState(false)
  const [recent, setRecent] = useState(false)
  const [moving, setMoving] = useState<DocumentItem | null>(null)
  // Multi-select. A Set rather than an array because every render asks "is
  // this one selected?" once per card, and a 500-document folder would turn
  // that into a linear scan per tile.
  const [sel, setSel] = useState<Set<number>>(() => new Set())
  const [bulkMove, setBulkMove] = useState(false)
  const [renaming, setRenaming] = useState<DocFolder | null>(null)
  // Which folder tile a drag is currently over, so it can light up. Null is
  // "none" — the top-level crumb uses -1, since 0 is a real folder id here.
  const [dropTarget, setDropTarget] = useState<number | null>(null)
  // Type and date. Kept out of `q` deliberately: these narrow a listing and a
  // search replaces it, and mixing them gave "search inside the filter" or
  // "filter inside the search" depending on which ran last.
  const [ftype, setFtype] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [filters, setFilters] = useState(false)
  // '' is the default order the server calls "smart": favourites first, then
  // newest. Choosing an explicit order turns the favourites float OFF, because
  // somebody who asked for "by name" means by name.
  const [sort, setSort] = useState('')

  // Pull the (user-editable) category list from masters; keep built-ins as fallback.
  useEffect(() => {
    api<{ items: MasterItem[] }>('/api/masters?type=document_category&active=1')
      .then((d) => { if (d.items.length) setCats(d.items.map((m) => ({ key: m.key, label: m.label, emoji: m.emoji || '📄' }))) })
      .catch(() => {})
  }, [])

  const load = useCallback(async (silent = false) => {
    if (!silent) setData(null)
    try {
      const params = new URLSearchParams()
      if (cat) params.set('category', cat)
      if (q.trim()) params.set('q', q.trim())
      if (sort) params.set('sort', sort)
      if (ftype) params.set('ftype', ftype)
      if (since) params.set('since', since)
      if (until) params.set('until', until)
      // Searching or filtering by category looks through the WHOLE tree, and
      // browsing shows one folder. They are different questions: a search
      // limited to the folder you happen to be standing in is the complaint
      // every file manager that did it has had, and a browse that flattened
      // the tree would make folders pointless.
      const searching = !!q.trim() || !!cat
      if (recent) {
        // Three lists from one call, flattened newest-first with duplicates
        // removed: a file that was both just added and just changed should
        // appear once, not twice.
        const r = await api<{ added: DocumentItem[]; changed: DocumentItem[]
                              starred: DocumentItem[] }>('/api/documents/recent')
        const seen = new Set<number>()
        const items = [...(r.changed || []), ...(r.added || [])]
          .filter((x) => (seen.has(x.id) ? false : (seen.add(x.id), true)))
        setData({ items, folders: [], path: [], total: items.length,
                  counts: {}, trashed: 0 })
        return
      }
      if (!searching) params.set('folder', String(folderId))
      const d = await api<DocumentsData>(`/api/documents?${params}`)
      setData(d)
    } catch { setData({ items: [], total: 0, counts: {}, trashed: 0 }) }
  }, [cat, q, folderId, recent, ftype, since, until, sort])
  useEffect(() => { load() }, [load])

  function pickFile(f: FileList | null) {
    if (f && f[0]) setAddFile(f[0])
  }

  async function createFolder(name: string) {
    try {
      await api('/api/documents/folders', {
        method: 'POST',
        body: { name, parent_id: folderId || null },
      })
      setNewFolder(false); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  const toggleSel = (id: number) =>
    setSel((old) => {
      const next = new Set(old)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  const clearSel = () => setSel(new Set())

  // Leaving the folder, searching or filtering clears the selection. Keeping
  // it would mean a bulk action firing on documents that are no longer on
  // screen — which is exactly the case where nobody can check what they are
  // about to do.
  useEffect(() => { clearSel() }, [folderId, q, cat, recent, ftype, since, until, sort])

  async function bulk(action: string, label: string) {
    const ids = [...sel]
    if (!ids.length) return
    try {
      const r = await api<{ changed: number }>('/api/documents/bulk',
        { method: 'POST', body: { ids, action } })
      // Reports what CHANGED, not what was asked for. They differ when a
      // document was already starred, or was trashed in another tab, and
      // saying "4 starred" over 2 real changes is the kind of small lie that
      // makes somebody stop trusting the counts.
      toast(`${r.changed} ${label}`)
      clearSel(); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  async function exportSelected() {
    const ids = [...sel]
    if (!ids.length) return
    try {
      // Downloaded through the API helper so the bearer token goes with it —
      // a plain <a href> to the endpoint is unauthenticated and comes back 401.
      const blob = await apiBlob('/api/documents/export', { method: 'POST', body: { ids } })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `documents-${new Date().toISOString().slice(0, 10)}.zip`
      document.body.appendChild(a); a.click(); a.remove()
      // Revoked on the next tick, not immediately: Safari has not started the
      // download yet when click() returns, and revoking first cancels it.
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
      toast(`${ids.length} file${ids.length === 1 ? '' : 's'} downloaded`)
      clearSel()
    } catch (e) { toast(errorMessage(e)) }
  }

  async function moveMany(target: number | null) {
    const ids = [...sel]
    try {
      await api('/api/documents/move', { method: 'POST', body: { ids, folder_id: target } })
      setBulkMove(false); clearSel(); toast(`${ids.length} moved`); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  /** Dropping onto a folder tile. Moves the whole selection when the dragged
      document is part of it, and just that one when it is not — which is what
      dragging one of several selected files does in every file manager. */
  async function dropOnto(id: number, target: number | null) {
    const ids = sel.has(id) ? [...sel] : [id]
    setDropTarget(null)
    try {
      await api('/api/documents/move', { method: 'POST', body: { ids, folder_id: target } })
      clearSel(); toast(`${ids.length} moved`); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  async function renameFolder(f: DocFolder, name: string) {
    try {
      await api(`/api/documents/folders/${f.id}`, { method: 'PUT', body: { name } })
      setRenaming(null); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  async function moveTo(d: DocumentItem, target: number | null) {
    try {
      await api('/api/documents/move', { method: 'POST', body: { ids: [d.id], folder_id: target } })
      setMoving(null); setView(null); toast('Moved'); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  async function trashFolder(f: DocFolder) {
    // Says what it will take with it. A folder delete that quietly binned
    // forty documents would be a nasty surprise, and the count is the only
    // thing that makes the confirmation worth reading.
    const extra = f.documents || f.folders
      ? ` and ${[f.documents && `${f.documents} document${f.documents === 1 ? '' : 's'}`,
                 f.folders && `${f.folders} folder${f.folders === 1 ? '' : 's'}`]
                 .filter(Boolean).join(' and ')} inside it`
      : ''
    if (!window.confirm(`Move “${f.name}”${extra} to the recycle bin?`)) return
    try {
      await api(`/api/documents/folders/${f.id}`, { method: 'DELETE' })
      toast('Moved to recycle bin'); load(true)
    } catch (e) { toast(errorMessage(e)) }
  }

  async function remove(d: DocumentItem) {
    await api(`/api/documents/${d.id}`, { method: 'DELETE' })
    setView(null); toast('Moved to recycle bin'); load(true)
  }
  async function toggleFav(d: DocumentItem) {
    const r = await api<{ is_favourite: number }>(`/api/documents/${d.id}/favourite`, { method: 'POST' })
    setView((v) => v && v.id === d.id ? { ...v, is_favourite: r.is_favourite } : v)
    load(true)
  }

  const items = data?.items ?? []
  const headerRight = (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <button className="icon-btn" onClick={() => setTrashOpen(true)} aria-label="Recycle bin" title="Recycle bin">
        🗑
        {!!data?.trashed && <span className="attn-badge">{data.trashed > 9 ? '9+' : data.trashed}</span>}
      </button>
      {canEdit && <button className="btn ghost sm" onClick={() => setScanning(true)}>📷 Scan</button>}
      {canEdit && <button className="btn sm" onClick={() => fileRef.current?.click()}>＋ Add</button>}
    </div>
  )

  if (scanning) return (
    <CatsCtx.Provider value={cats}>
      <ScanFlow cats={cats} onClose={() => setScanning(false)}
        onSaved={(d) => { setScanning(false); load(true); setView(d) }} />
    </CatsCtx.Provider>
  )

  if (trashOpen) return (
    <CatsCtx.Provider value={cats}>
      <DocTrash canEdit={canEdit} onBack={() => { setTrashOpen(false); load(true) }} />
    </CatsCtx.Provider>
  )

  return (
   <CatsCtx.Provider value={cats}>
    <div className="screen">
      <TopBar title="Documents" sub={data ? `${data.total} file${data.total === 1 ? '' : 's'} · secured` : undefined}
        onBack={canBack ? back : undefined} right={headerRight} />
      <input ref={fileRef} type="file" accept="*/*" hidden
        onChange={(e) => { pickFile(e.target.files); e.currentTarget.value = '' }} />

      {sel.size > 0 && (
        <div className="selbar">
          <button className="selbar-x" onClick={clearSel} aria-label="Clear selection">✕</button>
          <span className="selbar-n">{sel.size} selected</span>
          <button className="selbar-act" onClick={() => bulk('star', 'starred')} title="Star">★</button>
          <button className="selbar-act" onClick={() => bulk('unstar', 'unstarred')} title="Remove star">☆</button>
          <button className="selbar-act" onClick={exportSelected} title="Download as a zip">⤓</button>
          {canEdit && <button className="selbar-act" onClick={() => setBulkMove(true)} title="Move to a folder">⤴</button>}
          {canEdit && (
            <button className="selbar-act danger" onClick={() => bulk('trash', 'moved to the recycle bin')}
              title="Move to recycle bin">🗑</button>
          )}
        </div>
      )}

      <div className="doc-search">
        <input className="input" placeholder="Search documents…" value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="doc-cats">
        <button className={`chip${cat === '' && !recent ? ' on' : ''}`}
          onClick={() => { setCat(''); setRecent(false) }}>All</button>
        {/* Recent is a view, not a category — it cuts across all of them, which
            is why it sits here rather than in the folder tree. */}
        <button className={`chip${recent ? ' on' : ''}`}
          onClick={() => { setRecent((v) => !v); setCat('') }}>Recent</button>
        {cats.map((c) => {
          const n = data?.counts?.[c.key] || 0
          return (
            <button key={c.key} className={`chip${cat === c.key ? ' on' : ''}`} onClick={() => setCat(c.key)}>
              {c.emoji} {c.label}{n ? ` ${n}` : ''}
            </button>
          )
        })}
      </div>

      {/* Type and date. Behind a toggle because most visits do not need them
          and a row of always-open date inputs above a phone-sized list costs
          more screen than the folders it is meant to help find. */}
      <div className="doc-cats">
        <button className={`chip${filters || ftype || since || until ? ' on' : ''}`}
          onClick={() => setFilters((v) => !v)}>
          ⚙ Filters{(ftype ? 1 : 0) + (since || until ? 1 : 0) + (sort ? 1 : 0)
            ? ` (${(ftype ? 1 : 0) + (since || until ? 1 : 0) + (sort ? 1 : 0)})` : ''}
        </button>
        {(ftype || since || until || sort) && (
          <button className="chip"
            onClick={() => { setFtype(''); setSince(''); setUntil(''); setSort('') }}>
            Clear
          </button>
        )}
      </div>
      {filters && (
        <div className="doc-filters">
          <div className="doc-types">
            {FILE_TYPES.map((t) => (
              <button key={t.key} className={`chip${ftype === t.key ? ' on' : ''}`}
                onClick={() => setFtype((v) => (v === t.key ? '' : t.key))}>
                {t.emoji} {t.label}
              </button>
            ))}
          </div>
          <div className="doc-dates">
            <label>Sort
              <select className="inp rule-sel" value={sort}
                onChange={(e) => setSort(e.target.value)}>
                {SORTS.map((o) => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
            </label>
            <label>From <input className="inp" type="date" value={since}
              onChange={(e) => setSince(e.target.value)} /></label>
            <label>To <input className="inp" type="date" value={until}
              onChange={(e) => setUntil(e.target.value)} /></label>
          </div>
        </div>
      )}

      {/* The path back up. Hidden while searching, because search results come
          from the whole tree and a breadcrumb over them would name a folder
          most of the results are not in. */}
      {!q && !cat && (
        <div className="doc-crumbs">
          <button className="crumb" onClick={() => setFolderId(0)}>Documents</button>
          {(data?.path || []).map((c) => (
            <span key={c.id}>
              <span className="crumb-sep">›</span>
              <button className="crumb" onClick={() => setFolderId(c.id)}>{c.name}</button>
            </span>
          ))}
          {canEdit && (
            <button className="crumb-new" onClick={() => setNewFolder(true)}>
              + New folder
            </button>
          )}
        </div>
      )}

      <PullToRefresh onRefresh={() => load(true)}>
        {!data ? <Spinner />
          : items.length === 0 && !(data.folders || []).length
            ? <Empty icon="🗂️" title={q || cat ? 'No matches' : 'Nothing here yet'}
                hint={canEdit && !q && !cat
                  ? 'Add a document, or make a folder to put things in'
                  : undefined} />
            : <>
                {!!(data.folders || []).length && (
                  <div className="folder-grid">
                    {(data.folders || []).map((f) => (
                      <div key={f.id}
                        className={`folder-tile${dropTarget === f.id ? ' drop-on' : ''}`}
                        onDragOver={(e) => { e.preventDefault(); setDropTarget(f.id) }}
                        onDragLeave={() => setDropTarget((t) => (t === f.id ? null : t))}
                        onDrop={(e) => {
                          e.preventDefault()
                          const id = Number(e.dataTransfer.getData('text/document-id'))
                          if (id) dropOnto(id, f.id)
                        }}>
                        <button className="folder-hit" onClick={() => setFolderId(f.id)}>
                          <span className="folder-ic">📁</span>
                          <span className="folder-name">{f.name}</span>
                          <span className="folder-sub">
                            {f.folders ? `${f.folders} folder${f.folders === 1 ? '' : 's'}` : ''}
                            {f.folders && f.documents ? ' · ' : ''}
                            {f.documents ? `${f.documents} item${f.documents === 1 ? '' : 's'}` : ''}
                            {!f.folders && !f.documents ? 'Empty' : ''}
                          </span>
                        </button>
                        {canEdit && (
                          <button className="folder-ren" title="Rename"
                            onClick={() => setRenaming(f)}>✎</button>
                        )}
                        {canEdit && (
                          <button className="folder-x" title="Move to recycle bin"
                            onClick={() => trashFolder(f)}>✕</button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <div className="doc-grid">
                  {items.map((d) => (
                    <DocCard key={d.id} d={d}
                      selected={sel.has(d.id)}
                      selecting={sel.size > 0}
                      onToggle={() => toggleSel(d.id)}
                      draggable={canEdit}
                      onOpen={() => setView(d)}
                      onMove={canEdit ? () => setMoving(d) : undefined} />
                  ))}
                </div>
              </>}
      </PullToRefresh>

      {newFolder && (
        <NameFolderSheet onClose={() => setNewFolder(false)} onSave={createFolder} />
      )}
      {moving && (
        <MoveSheet what={`“${moving.title}”`} onClose={() => setMoving(null)}
          onPick={(target) => moveTo(moving, target)} />
      )}
      {bulkMove && (
        <MoveSheet what={`${sel.size} document${sel.size === 1 ? '' : 's'}`}
          onClose={() => setBulkMove(false)} onPick={moveMany} />
      )}
      {renaming && (
        <NameFolderSheet title="Rename folder" initial={renaming.name} action="Rename"
          onClose={() => setRenaming(null)}
          onSave={(name) => renameFolder(renaming, name)} />
      )}

      {addFile && <AddDoc file={addFile} onClose={() => setAddFile(null)}
        onSaved={() => { setAddFile(null); load(true) }} />}
      {view && <DocViewer d={view} canEdit={canEdit} onClose={() => setView(null)}
        onFav={() => toggleFav(view)} onDelete={() => remove(view)}
        onEdit={() => { setEdit(view); }} onChanged={() => load(true)} />}
      {edit && <EditDoc d={edit} onClose={() => setEdit(null)}
        onSaved={(u) => { setEdit(null); setView(u); load(true) }} />}
    </div>
   </CatsCtx.Provider>
  )
}

/* ---------- Recycle bin ---------- */

function DocTrash({ onBack, canEdit }: { onBack: () => void; canEdit: boolean }) {
  const toast = useToast()
  const cats = useCats()
  useOverlayBack(onBack)
  const [items, setItems] = useState<DocumentItem[] | null>(null)
  const [confirm, setConfirm] = useState<DocumentItem | null>(null)
  const [emptyOpen, setEmptyOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<{ items: DocumentItem[] }>('/api/documents/trash')
      .then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [])
  useEffect(() => { load() }, [load])

  async function restore(d: DocumentItem) {
    await api(`/api/documents/${d.id}/restore`, { method: 'POST' })
    setItems((x) => x?.filter((i) => i.id !== d.id) ?? null); toast('Restored')
  }
  async function destroy(d: DocumentItem) {
    await api(`/api/documents/${d.id}/permanent`, { method: 'DELETE' })
    setItems((x) => x?.filter((i) => i.id !== d.id) ?? null); setConfirm(null); toast('Deleted forever')
  }
  async function emptyBin() {
    setBusy(true)
    try {
      const r = await api<{ deleted: number }>('/api/documents/trash/empty', { method: 'POST' })
      setItems([]); setEmptyOpen(false)
      toast(`Deleted ${r.deleted} document${r.deleted === 1 ? '' : 's'} forever`)
    } catch { toast('Could not empty the bin') }
    finally { setBusy(false) }
  }

  const count = items?.length ?? 0
  return (
    <div className="screen">
      <TopBar title="Recycle bin" sub={items ? `${count} item${count === 1 ? '' : 's'}` : undefined} onBack={onBack}
        right={canEdit && count > 0 ? <button className="btn danger sm" onClick={() => setEmptyOpen(true)}>Empty</button> : undefined} />

      {!items ? <Spinner />
        : items.length === 0
          ? <Empty icon="🗑️" title="Recycle bin is empty" hint="Deleted documents land here first" />
          : (
            <>
              <p className="muted" style={{ fontSize: 12.5, margin: '0 2px 12px' }}>
                Restore a document to your locker, or delete it forever. Files stay safe until you do.
              </p>
              <div className="list">
                {items.map((d) => (
                  <div key={d.id} className="card" style={{ padding: 12 }}>
                    <div className="rowitem">
                      {d.thumb_url ? <AuthImg src={d.thumb_url} className="trash-thumb" />
                        : <div className="trash-thumb doc-fileicon"><span>{docIcon(d)}</span></div>}
                      <div className="main">
                        <div className="t">{d.title}</div>
                        <div className="s">{catMeta(cats, d.category).label}{d.trashed_fmt ? ` · deleted ${d.trashed_fmt}` : ''}</div>
                      </div>
                    </div>
                    {canEdit && (
                      <div className="swipe-actions">
                        <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => restore(d)}>↩ Restore</button>
                        <button className="btn danger sm" style={{ flex: 1 }} onClick={() => setConfirm(d)}>Delete forever</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

      {confirm && (
        <Sheet title="Delete forever?" onClose={() => setConfirm(null)}>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16 }}>
            <b>{confirm.title}</b> and its file will be permanently removed. This can’t be undone.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirm(null)}>Cancel</button>
            <button className="btn danger block" onClick={() => destroy(confirm)}>Delete forever</button>
          </div>
        </Sheet>
      )}
      {emptyOpen && (
        <Sheet title="Empty recycle bin?" onClose={() => setEmptyOpen(false)}>
          <div style={{ textAlign: 'center', fontSize: 40, marginBottom: 8 }}>🗑️</div>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16, textAlign: 'center' }}>
            This permanently deletes all <b>{count}</b> document{count === 1 ? '' : 's'} and their files.
            This <b>can’t be undone</b>.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setEmptyOpen(false)}>Cancel</button>
            <button className="btn danger block" disabled={busy} onClick={emptyBin}>{busy ? 'Deleting…' : `Delete all ${count}`}</button>
          </div>
        </Sheet>
      )}
    </div>
  )
}

function ExpiryBadge({ d }: { d: DocumentItem }) {
  if (!d.expiry_date || d.days_until_expiry == null) return null
  const s = d.expiry_status
  const txt = s === 'expired' ? 'Expired'
    : s === 'soon' ? `${d.days_until_expiry}d left`
      : `Valid · ${d.expiry_fmt}`
  return <span className={`doc-exp ${s}`}>{txt}</span>
}

function DocCard({ d, onOpen, onMove, selected, selecting, onToggle, draggable }: {
  d: DocumentItem; onOpen: () => void; onMove?: () => void
  selected?: boolean; selecting?: boolean; onToggle?: () => void; draggable?: boolean
}) {
  const meta = catMeta(useCats(), d.category)
  // A div wrapping a button, not a button wrapping everything: the move
  // affordance is itself a button, and nesting one inside another is invalid
  // HTML that browsers resolve by silently un-nesting, which breaks both.
  // Once ANYTHING is selected, a tap selects rather than opens. Mixing the
  // two — tap opens, tap-on-checkbox selects — is how people select four
  // files and then lose the lot by tapping the fifth.
  return (
    <div className={`doc-card${selected ? ' picked' : ''}`}
      draggable={!!draggable}
      onDragStart={(e) => {
        e.dataTransfer.setData('text/document-id', String(d.id))
        e.dataTransfer.effectAllowed = 'move'
      }}>
      {onToggle && (
        <button className={`doc-pick${selected ? ' on' : ''}`}
          aria-label={selected ? 'Deselect' : 'Select'}
          aria-pressed={!!selected}
          onClick={(e) => { e.stopPropagation(); onToggle() }}>
          {selected ? '✓' : ''}
        </button>
      )}
      <button className="doc-hit" onClick={() => (selecting && onToggle ? onToggle() : onOpen())}>
        <div className="doc-thumb">
          {d.thumb_url ? <AuthImg src={d.thumb_url} className="doc-thumb-img" />
            : <div className="doc-fileicon"><span>{docIcon(d)}</span><b>{(d.ext || 'file').toUpperCase()}</b></div>}
          {!!d.is_favourite && <span className="doc-star">★</span>}
          <ExpiryBadge d={d} />
        </div>
        <div className="doc-meta">
          <div className="doc-title">{d.title}</div>
          <div className="doc-sub">{meta.emoji} {meta.label}{d.doc_number ? ` · ${mask(d.doc_number)}` : ''}</div>
        </div>
      </button>
      {onMove && (
        <button className="doc-move" title="Move to a folder"
          onClick={(e) => { e.stopPropagation(); onMove() }}>⤴</button>
      )}
    </div>
  )
}


function NameFolderSheet({ onClose, onSave, title = 'New folder', initial = '',
                          action = 'Create' }: {
  onClose: () => void; onSave: (name: string) => void
  title?: string; initial?: string; action?: string
}) {
  const [name, setName] = useState(initial)
  return (
    <Sheet title={title} onClose={onClose}>
      <Field label="Name">
        <input className="inp" autoFocus value={name} maxLength={160}
          placeholder="Bank statements"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) onSave(name.trim()) }} />
      </Field>
      <button className="btn primary block" disabled={!name.trim()}
        onClick={() => onSave(name.trim())}>{action}</button>
    </Sheet>
  )
}


function MoveSheet({ what, onClose, onPick }: {
  /** What is being moved, already worded — one title, or "4 documents". */
  what: string; onClose: () => void; onPick: (folderId: number | null) => void
}) {
  const [folders, setFolders] = useState<DocFolder[] | null>(null)
  useEffect(() => {
    api<{ items: DocFolder[] }>('/api/documents/folders')
      .then((d) => setFolders(d.items)).catch(() => setFolders([]))
  }, [])

  // Indented by depth, so a flat list still reads as a tree. Depth is walked
  // from parent_id rather than stored, and bounded, because a cycle here would
  // otherwise hang the render rather than the request.
  const depthOf = (f: DocFolder, all: DocFolder[]): number => {
    let n = 0
    let cur = f.parent_id
    const seen = new Set<number>()
    while (cur && n < 32 && !seen.has(cur)) {
      seen.add(cur); n++
      cur = all.find((x) => x.id === cur)?.parent_id ?? null
    }
    return n
  }

  return (
    <Sheet title={`Move ${what}`} onClose={onClose}>
      {!folders ? <Spinner /> : (
        <div className="move-list">
          <button className="move-row" onClick={() => onPick(null)}>
            <span className="folder-ic">🗂️</span> Documents (top level)
          </button>
          {folders.map((f) => (
            <button key={f.id} className="move-row"
              style={{ paddingLeft: 12 + depthOf(f, folders) * 18 }}
              onClick={() => onPick(f.id)}>
              <span className="folder-ic">📁</span> {f.name}
            </button>
          ))}
          {!folders.length && (
            <p className="muted">No folders yet. Make one first.</p>
          )}
        </div>
      )}
    </Sheet>
  )
}

/** A text or CSV file shown in place, rather than offered as a download.
 *
 *  The server sends the first part and says whether it cut anything, so a
 *  40MB log does not cross the wire for a glance at the top of it. A CSV
 *  arrives already split into rows: the browser would otherwise need its own
 *  parser for quoting rules Python already has. */
function TextPreview({ d }: { d: DocumentItem }) {
  type P = { kind: 'text' | 'csv'; text?: string; rows?: string[][]
             truncated: boolean; size_bytes: number }
  const [p, setP] = useState<P | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let live = true
    api<P>(`/api/documents/${d.id}/preview`)
      .then((r) => { if (live) setP(r) })
      .catch((e) => { if (live) setErr(errorMessage(e)) })
    return () => { live = false }
  }, [d.id])

  if (err) return <div className="viewer-pdf"><div className="viewer-pdf-s">{err}</div></div>
  if (!p) return <div className="viewer-loading"><span className="spinner" /></div>

  return (
    <div className="txtprev">
      {p.kind === 'csv' && p.rows ? (
        <div className="txtprev-scroll">
          <table className="csvprev">
            <tbody>
              {p.rows.map((row, i) => (
                <tr key={i} className={i === 0 ? 'head' : undefined}>
                  {/* The first row is treated as a header for LOOKS only —
                      nothing downstream depends on it, because plenty of
                      exports have no header and styling the first data row
                      bold is a much smaller wrong than dropping it. */}
                  {row.map((cell, j) => <td key={j}>{cell}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <pre className="txtprev-scroll txtprev-pre">{p.text}</pre>
      )}
      {p.truncated && (
        <div className="txtprev-more">
          Showing the start of this file ({formatBytes(p.size_bytes)} in total).
          Download it to see the rest.
        </div>
      )}
    </div>
  )
}

/* ---------- Viewer ---------- */

function DocViewer({ d, canEdit, onClose, onFav, onDelete, onEdit, onChanged }: {
  d: DocumentItem; canEdit: boolean; onClose: () => void
  onFav: () => void; onDelete: () => void; onEdit: () => void
  /** Applying a read-out value edits the record, so the list behind must refresh. */
  onChanged: () => void
}) {
  useOverlayBack(onClose)
  const toast = useToast()
  const full = useAuthedBlob(d.is_image ? d.file_url : null)
  const [reveal, setReveal] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [chrome, setChrome] = useState(true)
  const [zoomed, setZoomed] = useState(false)
  const [details, setDetails] = useState(false)
  const [saving, setSaving] = useState(false)
  const [versions, setVersions] = useState(false)
  const [typing, setTyping] = useState(false)
  const meta = catMeta(useCats(), d.category)

  async function copy() {
    setSaving(true)
    try {
      await api(`/api/documents/${d.id}/copy`, { method: 'POST', body: {} })
      toast('Copied'); onChanged()
    } catch (e) { toast(errorMessage(e)) }
    finally { setSaving(false) }
  }

  async function openOrDownload(download: boolean) {
    setSaving(true)
    try {
      const r = await fetch(d.file_url, { headers: { Authorization: `Bearer ${tokenStore.get()}` } })
      if (!r.ok) throw new Error()
      const url = URL.createObjectURL(await r.blob())
      if (download) {
        const a = document.createElement('a')
        a.href = url; a.download = `${d.title}.${d.ext}`
        document.body.appendChild(a); a.click(); a.remove()
        toast('Saved to your device')
      } else {
        window.open(url, '_blank')
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch { toast('Could not open file') }
    finally { setSaving(false) }
  }

  const hasDetails = !!(d.doc_number || d.issue_fmt || d.expiry_fmt || d.notes)
  const showChrome = chrome && !zoomed

  return (
    <div className="viewer">
      <div className="viewer-stage">
        {d.is_text ? (
          <TextPreview d={d} />
        ) : !d.is_image ? (
          <div className="viewer-pdf">
            <div className="viewer-pdf-ic">📄</div>
            <div className="viewer-pdf-t">{d.title}</div>
            <div className="viewer-pdf-s">
              {(d.ext || 'file').toUpperCase()}{d.pages > 1 ? ` · ${d.pages} pages` : ''} · {Math.max(1, Math.round(d.size_bytes / 1024))} KB
            </div>
            <button className="btn" style={{ marginTop: 14 }} onClick={() => openOrDownload(false)}>
              {d.is_pdf ? 'Open PDF' : 'Download'}
            </button>
          </div>
        ) : full ? (
          <Zoomable fill src={full} alt={d.title}
            onZoomChange={setZoomed} onSingleTap={() => setChrome((c) => !c)} />
        ) : (
          <div className="viewer-loading"><span className="spinner" /></div>
        )}
      </div>

      <div className={`viewer-top${showChrome ? '' : ' hidden'}`}>
        <button className="viewer-btn" onClick={onClose} aria-label="Close">✕</button>
        <div className="viewer-title">
          <div className="vt-main">{d.title}</div>
          <div className="vt-sub">{meta.emoji} {meta.label}</div>
        </div>
        <button className="viewer-btn" onClick={onFav} aria-label="Favourite">{d.is_favourite ? '★' : '☆'}</button>
        <button className="viewer-btn" onClick={() => openOrDownload(true)} disabled={saving} aria-label="Download">
          {saving ? '…' : '⤓'}
        </button>
        {canEdit && <button className="viewer-btn" onClick={() => setVersions(true)}
          aria-label="Versions" title="Versions">↺</button>}
        {canEdit && <button className="viewer-btn" onClick={() => setTyping(true)}
          aria-label="What kind of document" title="What kind of document">Ἷ7</button>}
        {canEdit && <button className="viewer-btn" onClick={copy} disabled={saving}
          aria-label="Make a copy" title="Make a copy">⧉</button>}
        {canEdit && <button className="viewer-btn" onClick={onEdit} aria-label="Edit">✎</button>}
        {canEdit && <button className="viewer-btn danger" onClick={() => setConfirm(true)} aria-label="Delete">🗑</button>}
      </div>

      <div className={`viewer-bottom col${showChrome ? '' : ' hidden'}`}>
        <div className="vdoc-summary">
          {d.expiry_fmt && <ExpiryBadge d={d} />}
          <button className="vdoc-toggle" onClick={() => setDetails((v) => !v)}>
            {details ? 'Hide details' : 'Details'} <span className={details ? 'up' : ''}>⌃</span>
          </button>
        </div>

        {details && hasDetails && (
          <div className="vdoc-details">
            {d.doc_number && (
              <div className="vdoc-row">
                <span className="k">Number</span>
                <span className="v mono">{reveal ? d.doc_number : mask(d.doc_number)}</span>
                <button className="vdoc-reveal" onClick={() => setReveal((r) => !r)}>{reveal ? 'Hide' : 'Show'}</button>
              </div>
            )}
            {d.issue_fmt && <div className="vdoc-row"><span className="k">Issued</span><span className="v">{d.issue_fmt}</span></div>}
            {d.expiry_fmt && <div className="vdoc-row"><span className="k">Expires</span><span className="v">{d.expiry_fmt}</span></div>}
            {d.notes && <div className="vdoc-notes">{d.notes}</div>}
          </div>
        )}

        {details && <DocText id={d.id} onApplied={onChanged} />}
      </div>

      {versions && <VersionsSheet doc={d} onClose={() => setVersions(false)}
        onChanged={onChanged} />}
      {typing && <KindSheet doc={d} onClose={() => setTyping(false)}
        onChanged={onChanged} />}

      {confirm && (
        <Sheet title="Move to recycle bin?" onClose={() => setConfirm(false)}>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16 }}>
            <b>{d.title}</b> goes to the recycle bin. You can restore it from there, or delete
            it permanently once it’s in the bin.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirm(false)}>Cancel</button>
            <button className="btn danger block" onClick={onDelete}>Move to bin</button>
          </div>
        </Sheet>
      )}
    </div>
  )
}

/* ---------- Add / Edit ---------- */

function VersionsSheet({ doc, onClose, onChanged }: {
  doc: DocumentItem; onClose: () => void; onChanged: () => void
}) {
  const toast = useToast()
  type V = { id: number; version: number; orig_name: string | null; ext: string | null
             size_bytes: number; note: string | null; created_at: string | null }
  const [items, setItems] = useState<V[] | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    api<{ items: V[] }>(`/api/documents/${doc.id}/versions`)
      .then((r) => setItems(r.items || [])).catch(() => setItems([]))
  }, [doc.id])
  useEffect(() => { load() }, [load])

  async function replace(f: File) {
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('note', f.name)
      const r = await fetch(`/api/documents/${doc.id}/replace`, {
        method: 'POST', body: fd,
        headers: { Authorization: `Bearer ${tokenStore.get()}` },
      })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Failed')
      load(); onChanged(); toast('Replaced \u2014 the old file is kept as a version')
    } catch (e) { toast(errorMessage(e)) }
    finally { setBusy(false) }
  }

  /** Look at an old copy WITHOUT making it current.
   *
   *  The usual reason for opening this sheet is working out which version you
   *  want — and a Restore you have to perform first, to find out, is a Restore
   *  somebody then has to undo. */
  async function download(v: V) {
    setBusy(true)
    try {
      const blob = await apiBlob(`/api/documents/${doc.id}/versions/${v.version}/file`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = v.orig_name || `${doc.title} (v${v.version}).${v.ext || 'bin'}`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch (e) { toast(errorMessage(e)) }
    finally { setBusy(false) }
  }

  async function restore(v: number) {
    setBusy(true)
    try {
      await api(`/api/documents/${doc.id}/versions/${v}/restore`, { method: 'POST', body: {} })
      load(); onChanged(); toast(`Version ${v} is now the current file`)
    } catch (e) { toast(errorMessage(e)) }
    finally { setBusy(false) }
  }

  return (
    <Sheet title="Versions" onClose={onClose}>
      <p className="muted">
        Replacing a file keeps the old one, so nothing is lost by uploading the
        wrong scan. The last ten are kept.
      </p>
      <input ref={fileRef} type="file" className="file-offscreen"
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.currentTarget.value = ''
          if (f) replace(f)
        }} />
      <button className="btn block" disabled={busy}
        onClick={() => fileRef.current?.click()}>
        {busy ? 'Working\u2026' : 'Replace with a new file'}
      </button>

      {!items ? <Spinner /> : !items.length ? (
        <p className="muted" style={{ marginTop: 12 }}>No earlier versions yet.</p>
      ) : (
        <div className="ver-list">
          {items.map((v) => (
            <div key={v.id} className="ver-row">
              <div className="ver-main">
                <div className="ver-name">Version {v.version}</div>
                <div className="ver-sub">
                  {v.note || v.orig_name || ''}
                  {v.size_bytes ? ` \u00b7 ${formatBytes(v.size_bytes)}` : ''}
                  {v.created_at ? ` \u00b7 ${fmtDate(v.created_at)}` : ''}
                </div>
              </div>
              <button className="btn ghost sm" disabled={busy}
                title="Download this version without restoring it"
                onClick={() => download(v)}>↓</button>
              <button className="btn sm" disabled={busy}
                onClick={() => restore(v.version)}>Restore</button>
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}


function KindSheet({ doc, onClose, onChanged }: {
  doc: DocumentItem; onClose: () => void; onChanged: () => void
}) {
  const toast = useToast()
  const [kinds, setKinds] = useState<string[] | null>(null)
  useEffect(() => {
    api<{ items: string[] }>('/api/documents/kinds')
      .then((d) => setKinds(d.items || [])).catch(() => setKinds([]))
  }, [])

  async function set(kind: string | null) {
    try {
      await api(`/api/documents/${doc.id}/kind`, { method: 'POST', body: { kind } })
      onChanged(); onClose()
      toast(kind ? `Filed as ${kind.replace(/_/g, ' ')}` : 'Type cleared')
    } catch (e) { toast(errorMessage(e)) }
  }

  return (
    <Sheet title="What kind of document is this?" onClose={onClose}>
      <p className="muted">
        {doc.kind
          ? `Read as ${String(doc.kind).replace(/_/g, ' ')}${
              doc.kind_source === 'user' ? ', by you' : ' automatically'}.`
          : 'Not recognised automatically.'}
      </p>
      {!kinds ? <Spinner /> : (
        <div className="kind-grid">
          {kinds.map((k) => (
            <button key={k} className={`chip${doc.kind === k ? ' on' : ''}`}
              onClick={() => set(k)}>{k.replace(/_/g, ' ')}</button>
          ))}
        </div>
      )}
      <button className="btn ghost block" onClick={() => set(null)}
        style={{ marginTop: 12 }}>None of these</button>
    </Sheet>
  )
}


function CatPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const cats = useCats()
  return (
    <div className="doc-catpick">
      {cats.map((c) => (
        <button key={c.key} type="button" className={`doc-catopt${value === c.key ? ' on' : ''}`} onClick={() => onChange(c.key)}>
          <span>{c.emoji}</span>{c.label}
        </button>
      ))}
    </div>
  )
}

function AddDoc({ file, onClose, onSaved }: { file: File; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [title, setTitle] = useState(file.name.replace(/\.[^.]+$/, ''))
  const [cat, setCat] = useState('id')
  const [number, setNumber] = useState('')
  const [issue, setIssue] = useState('')
  const [expiry, setExpiry] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const isPdf = /pdf$/i.test(file.type) || /\.pdf$/i.test(file.name)
  const isImage = /^image\//i.test(file.type)
  const ext = (file.name.split('.').pop() || '').toLowerCase()
  const icon = isPdf ? '📄' : isImage ? '🖼️'
    : /^(xls|xlsx|csv|ods)$/.test(ext) ? '📊'
      : /^(doc|docx|odt|rtf|txt)$/.test(ext) ? '📝'
        : /^(zip|rar|7z)$/.test(ext) ? '🗜️' : '📎'
  // A zero-byte read is the browser saying it could not get the file, not that the
  // file is empty. On a Mac the usual cause is iCloud Drive: with "Optimise Storage"
  // on, the file shows in the picker but its contents live in the cloud until macOS
  // fetches them. Uploading would send nothing and fail server-side with a message
  // that explains none of this.
  const unreadable = file.size === 0

  async function submit() {
    if (unreadable) {
      toast('That file came back empty — open it once in Finder first, then try again')
      return
    }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('title', title)
      fd.append('category', cat)
      fd.append('doc_number', number)
      fd.append('issue_date', issue)
      fd.append('expiry_date', expiry)
      fd.append('notes', notes)
      const res = await fetch('/api/documents', { method: 'POST', headers: { Authorization: `Bearer ${tokenStore.get()}` }, body: fd })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'failed')
      toast('Document saved'); onSaved()
    } catch (e) { toast(e instanceof Error ? e.message : 'Upload failed') }
    finally { setBusy(false) }
  }

  return (
    <Sheet title="Add document" onClose={onClose}>
      <div className="doc-filechip">{icon} <span>{file.name}</span>
        <em>{unreadable ? '0 KB' : `${Math.max(1, Math.round(file.size / 1024))} KB`}</em></div>
      {unreadable && (
        <p className="form-hint warn">
          This file reads as empty, so there is nothing to upload. If it lives in
          iCloud Drive, open it once in Finder so macOS downloads it, then pick it again.
        </p>
      )}
      <Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Aadhaar Card" autoFocus /></Field>
      <Field label="Category"><CatPicker value={cat} onChange={setCat} /></Field>
      <Field label="Document number (optional)"><input className="input" value={number} onChange={(e) => setNumber(e.target.value)} placeholder="e.g. 1234 5678 9012" /></Field>
      <div className="row2">
        <Field label="Issue date"><input className="input" type="date" value={issue} onChange={(e) => setIssue(e.target.value)} /></Field>
        <Field label="Expiry date"><input className="input" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></Field>
      </div>
      <Field label="Notes (optional)"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      <button className="btn block" disabled={busy || !title.trim()} onClick={submit}>{busy ? 'Saving…' : 'Save document'}</button>
    </Sheet>
  )
}

function EditDoc({ d, onClose, onSaved }: { d: DocumentItem; onClose: () => void; onSaved: (u: DocumentItem) => void }) {
  const toast = useToast()
  const [title, setTitle] = useState(d.title)
  const [cat, setCat] = useState(d.category)
  const [number, setNumber] = useState(d.doc_number || '')
  const [issue, setIssue] = useState(d.issue_date || '')
  const [expiry, setExpiry] = useState(d.expiry_date || '')
  const [notes, setNotes] = useState(d.notes || '')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const r = await api<{ item: DocumentItem }>(`/api/documents/${d.id}`, {
        method: 'PUT',
        body: { title, category: cat, doc_number: number, issue_date: issue, expiry_date: expiry, notes },
      })
      toast('Saved'); onSaved(r.item)
    } catch { toast('Could not save') }
    finally { setBusy(false) }
  }

  return (
    <Sheet title="Edit document" onClose={onClose}>
      <Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="Category"><CatPicker value={cat} onChange={setCat} /></Field>
      <Field label="Document number"><input className="input" value={number} onChange={(e) => setNumber(e.target.value)} /></Field>
      <div className="row2">
        <Field label="Issue date"><input className="input" type="date" value={issue} onChange={(e) => setIssue(e.target.value)} /></Field>
        <Field label="Expiry date"><input className="input" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></Field>
      </div>
      <Field label="Notes"><textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      <button className="btn block" disabled={busy || !title.trim()} onClick={save}>{busy ? 'Saving…' : 'Save changes'}</button>
    </Sheet>
  )
}
