import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useNav } from '../nav'
import { useToast } from '../toast'
import { TopBar, Spinner, Empty, Sheet, Field } from '../ui'
import type { MasterItem, MasterTypeMeta } from '../types'

const EMOJI_SUGGEST = ['🪪', '💳', '🛡️', '🚗', '🏠', '🏥', '🎓', '📄', '✈️', '🏦', '🧾', '🔑', '📁', '⭐', '🩺', '📜']

export default function Masters() {
  const { back, canBack } = useNav()
  const toast = useToast()
  const [types, setTypes] = useState<MasterTypeMeta[] | null>(null)
  const [type, setType] = useState<MasterTypeMeta | null>(null)
  const [items, setItems] = useState<MasterItem[] | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [edit, setEdit] = useState<MasterItem | null>(null)
  const [listOpen, setListOpen] = useState<'new' | 'rename' | null>(null)

  // Keeps the same list selected across a reload, so renaming one or adding a
  // value does not bounce you back to the first tab.
  const loadTypes = useCallback(async (keep?: string) => {
    try {
      const d = await api<{ types: MasterTypeMeta[] }>('/api/masters/types')
      setTypes(d.types)
      setType(d.types.find((t) => t.type === keep) || d.types[0] || null)
    } catch { setTypes([]) }
  }, [])

  useEffect(() => { loadTypes() }, [loadTypes])

  async function removeList(t: MasterTypeMeta) {
    if (!confirm(`Delete the “${t.label}” list${t.count ? ` and its ${t.count} entries` : ''}?\n\nRecords already filed under those names keep them.`)) return
    try {
      await api(`/api/masters/types/${t.id}`, { method: 'DELETE' })
      toast('List deleted')
      loadTypes()
    } catch { toast('Could not delete that list') }
  }

  const load = useCallback(async () => {
    if (!type) return
    setItems(null)
    try {
      const d = await api<{ items: MasterItem[] }>(`/api/masters?type=${type.type}`)
      setItems(d.items)
    } catch { setItems([]) }
  }, [type])
  useEffect(() => { load() }, [load])

  async function toggleActive(m: MasterItem) {
    await api(`/api/masters/${m.id}`, { method: 'PUT', body: { is_active: m.is_active ? 0 : 1 } })
    setItems((xs) => xs?.map((x) => x.id === m.id ? { ...x, is_active: x.is_active ? 0 : 1 } : x) ?? null)
  }
  async function remove(m: MasterItem) {
    await api(`/api/masters/${m.id}`, { method: 'DELETE' })
    setItems((xs) => xs?.filter((x) => x.id !== m.id) ?? null); toast('Removed')
  }

  const headerRight = type
    ? <button className="btn sm" onClick={() => setAddOpen(true)}>＋ Add</button>
    : undefined

  return (
    <div className="screen">
      <TopBar title="Manage lists" sub="Categories, banks & more" onBack={canBack ? back : undefined} right={headerRight} />

      {!types ? <Spinner /> : (
        <>
          <div className="doc-cats">
            {types.map((t) => (
              <button key={t.type} className={`chip${type?.type === t.type ? ' on' : ''}`} onClick={() => setType(t)}>
                {t.icon ? `${t.icon} ` : ''}{t.label}
              </button>
            ))}
            {/* The lists themselves are editable now, not just their contents. */}
            <button className="chip" onClick={() => setListOpen('new')} title="Add a list of your own">＋ New list</button>
          </div>

          {type && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '10px 2px 4px' }}>
              <button className="btn ghost sm" onClick={() => setListOpen('rename')}>✏️ Rename this list</button>
              {/* A built-in is renameable but not removable — the expense form
                  asks for `expense_category` by name, so deleting it would
                  leave a field with nothing to offer and no way back. */}
              {type.is_builtin
                ? <span className="muted" style={{ fontSize: 12 }}>🔒 Built in — rename it, but it can’t be removed</span>
                : <button className="btn danger sm" onClick={() => removeList(type)}>Delete this list</button>}
            </div>
          )}

          <p className="muted" style={{ fontSize: 12.5, margin: '8px 2px 12px' }}>
            Add your own {type?.label.toLowerCase()}, rename or hide ones you don’t use. Hidden values stay off the pickers but keep past records intact.
          </p>

          {!items ? <Spinner />
            : items.length === 0 ? <Empty icon="📋" title="Nothing here yet" hint="Tap Add to create your first value" />
              : <div className="list">
                  {items.map((m) => (
                    <div key={m.id} className={`card mst-row${m.is_active ? '' : ' off'}`}>
                      <span className="mst-swatch" style={type?.field === 'color'
                        ? { background: m.color || 'var(--ink-faint)', color: '#fff' }
                        : { background: 'var(--bg)' }}>
                        {type?.field === 'color' ? (m.label[0] || '?').toUpperCase() : (m.emoji || '•')}
                      </span>
                      <div className="mst-main">
                        <div className="mst-label">{m.label}</div>
                        {!m.is_active && <div className="mst-hidden">Hidden</div>}
                      </div>
                      <button className="mst-act" onClick={() => toggleActive(m)} title={m.is_active ? 'Hide' : 'Show'}>
                        {m.is_active ? 'Hide' : 'Show'}
                      </button>
                      <button className="mst-act" onClick={() => setEdit(m)}>Edit</button>
                      <button className="mst-act danger" onClick={() => remove(m)}>✕</button>
                    </div>
                  ))}
                </div>}
        </>
      )}

      {addOpen && type && <MasterForm typeMeta={type} onClose={() => setAddOpen(false)}
        onSaved={() => { setAddOpen(false); load() }} />}
      {edit && type && <MasterForm typeMeta={type} item={edit} onClose={() => setEdit(null)}
        onSaved={() => { setEdit(null); load() }} />}
      {listOpen && <ListForm
        existing={listOpen === 'rename' ? type : null}
        onClose={() => setListOpen(null)}
        onSaved={(keep) => { setListOpen(null); loadTypes(keep) }} />}
    </div>
  )
}

/** Add or rename a LIST — not a value in one.
 *
 *  `type` is never editable, for a list somebody added as much as for a
 *  built-in: every value points at its list through that string, and the app's
 *  own forms name `expense_category` and `bank` in code. The label is what a
 *  person renames, and calling their bank list "Lenders" costs the product
 *  nothing. */
function ListForm({ existing, onClose, onSaved }: {
  existing: MasterTypeMeta | null; onClose: () => void; onSaved: (keep?: string) => void
}) {
  const toast = useToast()
  const [label, setLabel] = useState(existing?.label || '')
  const [icon, setIcon] = useState(existing?.icon || '')
  const [field, setField] = useState<'emoji' | 'color'>(existing?.field || 'emoji')
  const [busy, setBusy] = useState(false)
  const builtin = !!existing?.is_builtin

  async function submit() {
    if (!label.trim()) return
    setBusy(true)
    try {
      const body: Record<string, unknown> = { label: label.trim(), icon }
      // Not sent for a built-in: flipping it from emoji to colour would strip
      // the glyphs off values the product ships with. The server refuses it
      // anyway; not sending it keeps both halves saying the same thing.
      if (!builtin) body.field = field
      if (existing) {
        const d = await api<{ item: MasterTypeMeta }>(`/api/masters/types/${existing.id}`, { method: 'PUT', body })
        toast('Renamed'); onSaved(d.item.type)
      } else {
        const d = await api<{ item: MasterTypeMeta }>('/api/masters/types', { method: 'POST', body })
        toast('List created'); onSaved(d.item.type)
      }
    } catch { toast('Could not save that list') }
    finally { setBusy(false) }
  }

  return (
    <Sheet title={existing ? 'Rename this list' : 'Add a list'} onClose={onClose}>
      <p className="muted" style={{ fontSize: 12.5, marginTop: -4 }}>
        {existing
          ? 'Only the name changes. Everything in the list stays where it is.'
          : 'A list of your own — insurers, landlords, payment methods, whatever you keep track of.'}
      </p>
      <Field label="Name of the list">
        <input className="input" value={label} onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Insurers" autoFocus />
      </Field>
      {!builtin && (
        <Field label="What its entries carry">
          <select className="select" value={field} onChange={(e) => setField(e.target.value as 'emoji' | 'color')}>
            <option value="emoji">A symbol</option>
            <option value="color">A colour</option>
          </select>
        </Field>
      )}
      <Field label="Icon for the list itself (optional)">
        <div className="mst-emoji-suggest">
          {EMOJI_SUGGEST.map((e) => (
            <button key={e} type="button" className={`mst-emoji${icon === e ? ' on' : ''}`}
              onClick={() => setIcon(icon === e ? '' : e)}>{e}</button>
          ))}
        </div>
      </Field>
      <button className="btn block" disabled={busy || !label.trim()} onClick={submit}>
        {busy ? 'Saving…' : existing ? 'Save changes' : 'Create the list'}
      </button>
    </Sheet>
  )
}

function MasterForm({ typeMeta, item, onClose, onSaved }: {
  typeMeta: MasterTypeMeta; item?: MasterItem; onClose: () => void; onSaved: () => void
}) {
  const toast = useToast()
  const [label, setLabel] = useState(item?.label || '')
  const [emoji, setEmoji] = useState(item?.emoji || '')
  const [color, setColor] = useState(item?.color || '#0d9488')
  const [busy, setBusy] = useState(false)
  const isColor = typeMeta.field === 'color'

  async function submit() {
    if (!label.trim()) return
    setBusy(true)
    try {
      const body: Record<string, unknown> = { type: typeMeta.type, label: label.trim() }
      if (isColor) body.color = color; else body.emoji = emoji.trim()
      if (item) await api(`/api/masters/${item.id}`, { method: 'PUT', body })
      else await api('/api/masters', { method: 'POST', body })
      toast(item ? 'Saved' : 'Added'); onSaved()
    } catch { toast('Could not save') }
    finally { setBusy(false) }
  }

  return (
    <Sheet title={item ? 'Edit value' : `Add ${typeMeta.label.replace(/s$/, '').toLowerCase()}`} onClose={onClose}>
      <Field label="Name">
        <input className="input" value={label} onChange={(e) => setLabel(e.target.value)}
          placeholder={isColor ? 'e.g. Bank of Baroda' : 'e.g. Travel documents'} autoFocus />
      </Field>
      {isColor ? (
        <Field label="Colour">
          <div className="mst-colorrow">
            <input type="color" className="mst-colorpick" value={color} onChange={(e) => setColor(e.target.value)} />
            <span className="mst-swatch" style={{ background: color, color: '#fff' }}>{(label[0] || 'B').toUpperCase()}</span>
            <span className="muted" style={{ fontSize: 13 }}>{color}</span>
          </div>
        </Field>
      ) : (
        <Field label="Icon (emoji)">
          <input className="input" value={emoji} onChange={(e) => setEmoji(e.target.value)} placeholder="Pick or type an emoji" maxLength={4} />
          <div className="mst-emoji-suggest">
            {EMOJI_SUGGEST.map((e) => (
              <button key={e} type="button" className={`mst-emoji${emoji === e ? ' on' : ''}`} onClick={() => setEmoji(e)}>{e}</button>
            ))}
          </div>
        </Field>
      )}
      <button className="btn block" disabled={busy || !label.trim()} onClick={submit}>
        {busy ? 'Saving…' : item ? 'Save changes' : 'Add'}
      </button>
    </Sheet>
  )
}
