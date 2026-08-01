import { useState } from 'react'
import { api } from '../api'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { Sheet, Field } from '../ui'
import { HistoryLink, ModuleScreen } from './Scaffold'
import type { VaultItem } from '../types'

const CATS = ['Login', 'Banking', 'Card', 'Email', 'Social', 'Work', 'Wifi', 'Other']
// Per-category tile colour (password-manager style).
const CAT_COLOR: Record<string, string> = {
  Login: '#6366f1', Banking: '#10b981', Card: '#ec4899', Email: '#0ea5e9',
  Social: '#8b5cf6', Work: '#64748b', Wifi: '#14b8a6', Other: '#f59e0b',
}
const colorFor = (c?: string | null) => CAT_COLOR[c || ''] || '#64748b'

export default function Vault() {
  const { items, loading, refresh, create, update, remove, error, reload} = useResource<VaultItem>('/api/vault')
  const toast = useToast()
  const [edit, setEdit] = useState<VaultItem | null | 'new'>(null)
  const [revealed, setRevealed] = useState<Record<number, string>>({})

  async function reveal(id: number) {
    if (revealed[id]) { setRevealed((r) => { const n = { ...r }; delete n[id]; return n }); return }
    try {
      const { password } = await api<{ password: string }>(`/api/vault/${id}/reveal`, { method: 'POST' })
      setRevealed((r) => ({ ...r, [id]: password }))
    } catch (e) { toast(e instanceof Error ? e.message : 'Cannot reveal') }
  }

  async function save(body: Partial<VaultItem> & { password?: string }) {
    if (edit === 'new') { await create(body); toast('Saved to vault') }
    else if (edit) { await update(edit.id, body); toast('Updated') }
    setEdit(null)
  }
  async function del(id: number) { await remove(id); toast('Deleted'); setEdit(null) }

  return (
    <ModuleScreen mod="vault" sub={`${items.length} items · AES-256 encrypted`} loading={loading} empty={items.length === 0} onAdd={() => setEdit('new')} error={error} onRetry={reload} onRefresh={refresh}>
      {items.map((v) => {
        const col = colorFor(v.category)
        const letter = (v.title || '?').trim().charAt(0).toUpperCase()
        const shown = revealed[v.id]
        return (
          <div key={v.id}>
            <div className="vcard">
              <button className="vcard-hit" onClick={() => setEdit(v)}>
                <div className="vault-avatar" style={{ background: `linear-gradient(135deg, ${col}, color-mix(in srgb, ${col} 62%, #000))` }}>{letter}</div>
                <div className="vcard-main">
                  <div className="vcard-title">{v.title}</div>
                  <div className="vcard-sub">{v.username || 'No username'}</div>
                </div>
              </button>
              {v.category && <span className="pill muted vcard-cat">{v.category}</span>}
              {v.has_password && <button className="btn ghost sm" onClick={() => reveal(v.id)}>{shown ? 'Hide' : 'Reveal'}</button>}
            </div>
            {shown && (
              <div className="reveal-box">
                <span className="mono">{shown}</span>
                <button className="reveal-copy" onClick={() => { navigator.clipboard?.writeText(shown); toast('Password copied') }}>Copy</button>
              </div>
            )}
          </div>
        )
      })}
      {edit && <VaultForm initial={edit === 'new' ? null : edit} onSave={save} onDelete={del} onClose={() => setEdit(null)} />}
    </ModuleScreen>
  )
}

function VaultForm({ initial, onSave, onDelete, onClose }: {
  initial: VaultItem | null; onSave: (b: Partial<VaultItem> & { password?: string }) => void; onDelete: (id: number) => void; onClose: () => void
}) {
  const [f, setF] = useState<Partial<VaultItem> & { password?: string }>(initial || { category: 'Login' })
  const [showPw, setShowPw] = useState(false)
  const set = (k: string, v: unknown) => setF((p) => ({ ...p, [k]: v }))
  return (
    <Sheet title={initial ? 'Edit item' : 'Add to vault'} onClose={onClose}>
      <Field label="Title"><input className="input" value={f.title || ''} onChange={(e) => set('title', e.target.value)} placeholder="Gmail" /></Field>
      <div className="row2">
        <Field label="Username / email"><input className="input" value={f.username || ''} onChange={(e) => set('username', e.target.value)} /></Field>
        <Field label="Category"><select className="select" value={f.category || 'Login'} onChange={(e) => set('category', e.target.value)}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
      </div>
      <Field label="URL (optional)"><input className="input" value={f.url || ''} onChange={(e) => set('url', e.target.value)} placeholder="https://" /></Field>
      <Field label={initial ? 'Password (leave blank to keep)' : 'Password'}>
        <div className="pw-wrap">
          <input className="input" type={showPw ? 'text' : 'password'} autoComplete="new-password"
            value={f.password || ''} onChange={(e) => set('password', e.target.value)} placeholder="••••••••" />
          <button type="button" className="pw-toggle" onClick={() => setShowPw((s) => !s)}>{showPw ? 'Hide' : 'Show'}</button>
        </div>
      </Field>
      <button className="btn block" onClick={() => onSave(f)} disabled={!f.title}>{initial ? 'Save changes' : 'Save to vault'}</button>
      {initial && <HistoryLink entity="vault" id={initial.id} label={initial.title} block />}
      {initial && <button className="btn danger block" style={{ marginTop: 10 }} onClick={() => onDelete(initial.id)}>Delete item</button>}
    </Sheet>
  )
}
