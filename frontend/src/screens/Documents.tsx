import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { DocText } from '../DocText'
import { api, tokenStore } from '../api'
import { useNav, useOverlayBack } from '../nav'
import { useAuth } from '../auth'
import { useToast } from '../toast'
import { TopBar, Spinner, Empty, Sheet, Field } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { Zoomable } from '../Zoomable'
import { ScanFlow } from './Scan'
import type { DocumentItem, DocumentsData, MasterItem } from '../types'

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
      const d = await api<DocumentsData>(`/api/documents?${params}`)
      setData(d)
    } catch { setData({ items: [], total: 0, counts: {}, trashed: 0 }) }
  }, [cat, q])
  useEffect(() => { load() }, [load])

  function pickFile(f: FileList | null) {
    if (f && f[0]) setAddFile(f[0])
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

      <div className="doc-search">
        <input className="input" placeholder="Search documents…" value={q}
          onChange={(e) => setQ(e.target.value)} />
      </div>

      <div className="doc-cats">
        <button className={`chip${cat === '' ? ' on' : ''}`} onClick={() => setCat('')}>All</button>
        {cats.map((c) => {
          const n = data?.counts?.[c.key] || 0
          return (
            <button key={c.key} className={`chip${cat === c.key ? ' on' : ''}`} onClick={() => setCat(c.key)}>
              {c.emoji} {c.label}{n ? ` ${n}` : ''}
            </button>
          )
        })}
      </div>

      <PullToRefresh onRefresh={() => load(true)}>
        {!data ? <Spinner />
          : items.length === 0
            ? <Empty icon="🗂️" title={q || cat ? 'No matches' : 'No documents yet'}
                hint={canEdit && !q && !cat ? 'Tap Add to save an ID card, policy or certificate' : undefined} />
            : <div className="doc-grid">
                {items.map((d) => <DocCard key={d.id} d={d} onOpen={() => setView(d)} />)}
              </div>}
      </PullToRefresh>

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

function DocCard({ d, onOpen }: { d: DocumentItem; onOpen: () => void }) {
  const meta = catMeta(useCats(), d.category)
  return (
    <button className="doc-card" onClick={onOpen}>
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
  const meta = catMeta(useCats(), d.category)

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
        {!d.is_image ? (
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
