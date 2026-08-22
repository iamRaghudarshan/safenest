// Notes — a Google-Keep-style module. A masonry of coloured cards (notes and
// checklists), pinned ones first, with an editor sheet, colours, labels, pin,
// archive and a recycle bin, and search across titles, bodies and checklist lines.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useToast } from '../toast'
import { Sheet, Field } from '../ui'
import type { Note, NoteItem } from '../types'

// Keep's pastels. Fixed (not theme vars) because a note's colour is its identity
// and should read the same in light and dark; text is forced dark to suit them.
const COLORS: { key: string; bg: string }[] = [
  { key: 'default', bg: '' },
  { key: 'red', bg: '#faafa8' },
  { key: 'orange', bg: '#f39f76' },
  { key: 'yellow', bg: '#fff8b8' },
  { key: 'green', bg: '#e2f6d3' },
  { key: 'teal', bg: '#b4ddd3' },
  { key: 'blue', bg: '#d4e4ed' },
  { key: 'darkblue', bg: '#aeccdc' },
  { key: 'purple', bg: '#d3bfdb' },
  { key: 'pink', bg: '#f6e2dd' },
  { key: 'brown', bg: '#e9e3d4' },
  { key: 'grey', bg: '#efeff1' },
]
const bgOf = (c: string) => COLORS.find((x) => x.key === c)?.bg || ''

type Bucket = 'active' | 'archived' | 'trashed'

export default function Notes() {
  const toast = useToast()
  const [notes, setNotes] = useState<Note[]>([])
  const [labels, setLabels] = useState<string[]>([])
  const [bucket, setBucket] = useState<Bucket>('active')
  const [label, setLabel] = useState('')
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Note | 'new' | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams({ bucket })
      if (label) p.set('label', label)
      if (q.trim()) p.set('q', q.trim())
      const d = await api<{ items: Note[]; labels: string[] }>(`/api/notes?${p}`)
      setNotes(d.items || [])
      setLabels(d.labels || [])
    } catch { setNotes([]) } finally { setLoading(false) }
  }, [bucket, label, q])
  useEffect(() => { load() }, [load])

  async function act(id: number, path: string) {
    try { await api(`/api/notes/${id}/${path}`, { method: 'POST' }); load() }
    catch { toast('Could not do that') }
  }
  async function del(id: number) {
    try { await api(`/api/notes/${id}`, { method: 'DELETE' }); toast('Deleted'); load() }
    catch { toast('Could not delete') }
  }
  async function save(body: Partial<Note>, id?: number) {
    try {
      if (id) await api(`/api/notes/${id}`, { method: 'PUT', body })
      else await api('/api/notes', { method: 'POST', body })
      setEditing(null); load()
    } catch { toast('Could not save the note') }
  }

  const pinned = notes.filter((n) => n.pinned)
  const others = notes.filter((n) => !n.pinned)

  return (
    <div className="screen">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 2px 12px' }}>
        <h2 style={{ margin: 0, flex: '0 0 auto' }}>Notes</h2>
        <input className="input" placeholder="Search notes…" value={q}
          onChange={(e) => setQ(e.target.value)} style={{ flex: 1, maxWidth: 360 }} />
        {bucket === 'active' && (
          <button className="btn sm" onClick={() => setEditing('new')}>＋ New</button>
        )}
      </div>

      {/* Buckets — Notes / Archive / Bin */}
      <div className="doc-cats" style={{ marginBottom: 4 }}>
        {(['active', 'archived', 'trashed'] as Bucket[]).map((b) => (
          <button key={b} className={`chip${bucket === b ? ' on' : ''}`}
            onClick={() => { setBucket(b); setLabel('') }}>
            {b === 'active' ? 'Notes' : b === 'archived' ? 'Archive' : 'Bin'}
          </button>
        ))}
      </div>

      {/* Label rail */}
      {labels.length > 0 && (
        <div className="doc-cats">
          <button className={`chip${label === '' ? ' on' : ''}`} onClick={() => setLabel('')}>All</button>
          {labels.map((l) => (
            <button key={l} className={`chip${label === l ? ' on' : ''}`} onClick={() => setLabel(l)}>🏷 {l}</button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="muted" style={{ padding: 24 }}>Loading…</div>
      ) : notes.length === 0 ? (
        <div className="empty" style={{ padding: 40, textAlign: 'center', color: 'var(--ink-soft)' }}>
          <div style={{ fontSize: 34 }}>💡</div>
          {bucket === 'active' ? 'No notes yet — tap New to jot one down or start a checklist.'
            : bucket === 'archived' ? 'Nothing archived.' : 'The bin is empty.'}
        </div>
      ) : (
        bucket === 'active' && pinned.length > 0 ? (
          <>
            <div className="section-title">Pinned</div>
            <Masonry notes={pinned} onOpen={setEditing} onAct={act} onDel={del} bucket={bucket} />
            {others.length > 0 && (
              <>
                <div className="section-title">Others</div>
                <Masonry notes={others} onOpen={setEditing} onAct={act} onDel={del} bucket={bucket} />
              </>
            )}
          </>
        ) : (
          <Masonry notes={notes} onOpen={setEditing} onAct={act} onDel={del} bucket={bucket} />
        )
      )}

      {editing && (
        <NoteEditor note={editing === 'new' ? null : editing}
          onSave={save} onClose={() => setEditing(null)}
          onAct={act} onDel={del} />
      )}
    </div>
  )
}

function Masonry({ notes, onOpen, onAct, onDel, bucket }: {
  notes: Note[]; onOpen: (n: Note) => void; bucket: Bucket
  onAct: (id: number, path: string) => void; onDel: (id: number) => void
}) {
  if (notes.length === 0) return null
  return (
    <div style={{ columns: '230px', columnGap: 12, marginTop: 6 }}>
      {notes.map((n) => (
        <NoteCard key={n.id} n={n} onOpen={() => onOpen(n)} onAct={onAct} onDel={onDel} bucket={bucket} />
      ))}
    </div>
  )
}

function NoteCard({ n, onOpen, onAct, onDel, bucket }: {
  n: Note; onOpen: () => void; bucket: Bucket
  onAct: (id: number, path: string) => void; onDel: (id: number) => void
}) {
  const bg = bgOf(n.color)
  const done = n.items.filter((i) => i.checked).length
  return (
    <div style={{
      breakInside: 'avoid', marginBottom: 12, borderRadius: 12, padding: '12px 14px',
      background: bg || 'var(--card)', color: bg ? '#202124' : 'inherit',
      border: `1px solid ${bg ? 'transparent' : 'var(--line)'}`, cursor: 'pointer',
    }} onClick={onOpen}>
      {n.title && <div style={{ fontWeight: 700, marginBottom: 6 }}>{n.title}</div>}
      {n.kind === 'checklist' ? (
        <div>
          {n.items.slice(0, 8).map((it, i) => (
            <div key={i} style={{ display: 'flex', gap: 7, alignItems: 'flex-start', fontSize: 13.5, opacity: it.checked ? 0.6 : 1 }}>
              <span>{it.checked ? '☑' : '☐'}</span>
              <span style={{ textDecoration: it.checked ? 'line-through' : 'none' }}>{it.text}</span>
            </div>
          ))}
          {n.items.length > 8 && <div style={{ fontSize: 12, opacity: 0.7 }}>+{n.items.length - 8} more</div>}
          {n.items.length > 0 && <div style={{ fontSize: 11.5, opacity: 0.7, marginTop: 4 }}>{done}/{n.items.length} done</div>}
        </div>
      ) : (
        n.body && <div style={{ fontSize: 13.5, whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'hidden' }}>{n.body}</div>
      )}
      {n.labels.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
          {n.labels.map((l) => <span key={l} className="pill" style={{ fontSize: 11 }}>{l}</span>)}
        </div>
      )}
      <div style={{ display: 'flex', gap: 4, marginTop: 8, justifyContent: 'flex-end' }} onClick={(e) => e.stopPropagation()}>
        {bucket === 'trashed' ? (
          <>
            <IconBtn title="Restore" onClick={() => onAct(n.id, 'restore')}>↩</IconBtn>
            <IconBtn title="Delete for good" onClick={() => onDel(n.id)}>🗑</IconBtn>
          </>
        ) : (
          <>
            <IconBtn title={n.pinned ? 'Unpin' : 'Pin'} onClick={() => onAct(n.id, 'pin')}>{n.pinned ? '📌' : '📍'}</IconBtn>
            <IconBtn title={bucket === 'archived' ? 'Unarchive' : 'Archive'} onClick={() => onAct(n.id, 'archive')}>🗄</IconBtn>
            <IconBtn title="Move to bin" onClick={() => onAct(n.id, 'trash')}>🗑</IconBtn>
          </>
        )}
      </div>
    </div>
  )
}

function IconBtn({ children, title, onClick }: { children: React.ReactNode; title: string; onClick: () => void }) {
  return (
    <button title={title} onClick={onClick} style={{
      border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 15,
      padding: '2px 5px', borderRadius: 7, lineHeight: 1,
    }}>{children}</button>
  )
}

function NoteEditor({ note, onSave, onClose, onAct, onDel }: {
  note: Note | null
  onSave: (body: Partial<Note>, id?: number) => void
  onClose: () => void
  onAct: (id: number, path: string) => void
  onDel: (id: number) => void
}) {
  const [title, setTitle] = useState(note?.title || '')
  const [kind, setKind] = useState<'note' | 'checklist'>(note?.kind || 'note')
  const [body, setBody] = useState(note?.body || '')
  const [color, setColor] = useState(note?.color || 'default')
  const [labels, setLabels] = useState<string[]>(note?.labels || [])
  const [labelInput, setLabelInput] = useState('')
  const [items, setItems] = useState<NoteItem[]>(note?.items?.length ? note.items : [{ text: '', checked: false }])
  const [pinned, setPinned] = useState(!!note?.pinned)

  function setItem(i: number, patch: Partial<NoteItem>) {
    setItems((xs) => xs.map((it, j) => j === i ? { ...it, ...patch } : it))
  }
  function addItem() { setItems((xs) => [...xs, { text: '', checked: false }]) }
  function removeItem(i: number) { setItems((xs) => xs.filter((_, j) => j !== i)) }
  function addLabelFromInput() {
    const l = labelInput.trim()
    if (l && !labels.includes(l)) setLabels((xs) => [...xs, l])
    setLabelInput('')
  }

  function submit() {
    const body_: Partial<Note> = {
      title, kind, color, labels, pinned,
      body: kind === 'note' ? body : '',
      items: kind === 'checklist' ? items.filter((it) => it.text.trim()) : [],
    }
    onSave(body_, note?.id)
  }

  return (
    <Sheet title={note ? 'Edit note' : 'New note'} onClose={onClose}>
      <Field label="Title"><input className="input" value={title} autoFocus
        onChange={(e) => setTitle(e.target.value)} placeholder="Title" /></Field>

      {/* Note ⇄ Checklist */}
      <div className="doc-cats" style={{ marginBottom: 6 }}>
        <button className={`chip${kind === 'note' ? ' on' : ''}`} onClick={() => setKind('note')}>📝 Note</button>
        <button className={`chip${kind === 'checklist' ? ' on' : ''}`} onClick={() => setKind('checklist')}>☑ Checklist</button>
      </div>

      {kind === 'note' ? (
        <Field label="Note"><textarea className="input" rows={6} value={body}
          onChange={(e) => setBody(e.target.value)} placeholder="Write something…" /></Field>
      ) : (
        <Field label="Items">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {items.map((it, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <input type="checkbox" checked={it.checked} onChange={(e) => setItem(i, { checked: e.target.checked })} />
                <input className="input" value={it.text} placeholder="List item"
                  onChange={(e) => setItem(i, { text: e.target.value })}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addItem() } }}
                  style={{ flex: 1, textDecoration: it.checked ? 'line-through' : 'none' }} />
                <button className="btn ghost sm" onClick={() => removeItem(i)}>×</button>
              </div>
            ))}
            <button className="btn ghost sm" onClick={addItem} style={{ alignSelf: 'flex-start' }}>＋ Add item</button>
          </div>
        </Field>
      )}

      {/* Colour */}
      <Field label="Colour">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {COLORS.map((c) => (
            <button key={c.key} title={c.key} onClick={() => setColor(c.key)} style={{
              width: 28, height: 28, borderRadius: 999, cursor: 'pointer',
              background: c.bg || 'var(--card)',
              border: color === c.key ? '2px solid var(--brand)' : '1px solid var(--line)',
            }} />
          ))}
        </div>
      </Field>

      {/* Labels */}
      <Field label="Labels">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
          {labels.map((l) => (
            <span key={l} className="pill" style={{ cursor: 'pointer' }}
              onClick={() => setLabels((xs) => xs.filter((x) => x !== l))}>{l} ×</span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="input" value={labelInput} placeholder="Add a label"
            onChange={(e) => setLabelInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addLabelFromInput() } }} style={{ flex: 1 }} />
          <button className="btn ghost sm" onClick={addLabelFromInput}>Add</button>
        </div>
      </Field>

      <label style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '8px 0' }}>
        <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Pin to top
      </label>

      <button className="btn block" onClick={submit}
        disabled={!title.trim() && !body.trim() && !items.some((i) => i.text.trim())}>
        {note ? 'Save changes' : 'Create note'}
      </button>

      {note && (
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button className="btn ghost sm" onClick={() => { onAct(note.id, 'archive'); onClose() }}>🗄 Archive</button>
          <button className="btn ghost sm" onClick={() => { onDel(note.id); onClose() }}>🗑 Delete</button>
        </div>
      )}
    </Sheet>
  )
}
