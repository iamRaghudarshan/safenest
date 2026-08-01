import { useState } from 'react'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { money, fmtDate, dueLabel } from '../format'
import { Sheet, Field, Money } from '../ui'
import { IcShield } from '../icons'
import { HistoryLink, ModuleScreen } from './Scaffold'
import type { Insurance as Ins } from '../types'

const TYPES = ['Life', 'Health', 'Term', 'Motor', 'Home', 'Travel']
const FREQ = ['monthly', 'quarterly', 'half-yearly', 'yearly']

export default function Insurance() {
  const { items, loading, refresh, create, update, remove, error, reload} = useResource<Ins>('/api/insurance')
  const toast = useToast()
  const [edit, setEdit] = useState<Ins | null | 'new'>(null)

  async function save(body: Partial<Ins>) {
    if (edit === 'new') { await create(body); toast('Policy added') }
    else if (edit) { await update(edit.id, body); toast('Policy updated') }
    setEdit(null)
  }
  async function del(id: number) { await remove(id); toast('Policy removed'); setEdit(null) }

  return (
    <ModuleScreen mod="insurance" sub={`${items.length} policies`} loading={loading} empty={items.length === 0} onAdd={() => setEdit('new')} error={error} onRetry={reload} onRefresh={refresh}>
      {items.map((p) => {
        const days = p.renewal_date ? Math.ceil((+new Date(p.renewal_date) - +new Date()) / 864e5) : null
        const dl = dueLabel(days)
        return (
          <button key={p.id} className="mcard" onClick={() => setEdit(p)}>
            <div className="mcard-head">
              <div className="mcard-ic" style={{ background: 'var(--c-insurance)' }}><IcShield /></div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mcard-title">{p.provider}</div>
                <div className="mcard-sub">{p.policy_type} · #{p.policy_no || '—'}</div>
              </div>
              {days != null && <span className={`pill ${dl.tone}`}>{days < 0 ? 'Expired' : dl.text.replace('In ', 'Renews in ')}</span>}
            </div>
            <div className="mcard-row">
              <div>
                <div className="mcard-label">Cover</div>
                <div className="mcard-big">{money(p.sum_assured)}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="mcard-label">Premium</div>
                <div className="mcard-val">{money(p.premium)} <span className="muted" style={{ fontWeight: 500, fontSize: 12 }}>/{p.frequency}</span></div>
              </div>
            </div>
            <div className="mcard-foot">
              <span>Renews {fmtDate(p.renewal_date)}</span>
            </div>
          </button>
        )
      })}
      {edit && <InsForm initial={edit === 'new' ? null : edit} onSave={save} onDelete={del} onClose={() => setEdit(null)} />}
    </ModuleScreen>
  )
}

function InsForm({ initial, onSave, onDelete, onClose }: { initial: Ins | null; onSave: (b: Partial<Ins>) => void; onDelete: (id: number) => void; onClose: () => void }) {
  const [f, setF] = useState<Partial<Ins>>(initial || { policy_type: 'Life', frequency: 'yearly' })
  const set = (k: keyof Ins, v: unknown) => setF((p) => ({ ...p, [k]: v }))
  return (
    <Sheet title={initial ? 'Edit policy' : 'Add policy'} onClose={onClose}>
      <Field label="Provider"><input className="input" value={f.provider || ''} onChange={(e) => set('provider', e.target.value)} placeholder="LIC / HDFC Ergo" /></Field>
      <div className="row2">
        <Field label="Type"><select className="select" value={f.policy_type || 'Life'} onChange={(e) => set('policy_type', e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Policy no."><input className="input" value={f.policy_no || ''} onChange={(e) => set('policy_no', e.target.value)} /></Field>
      </div>
      <div className="row2">
        <Field label="Premium"><Money value={f.premium ?? ''} onChange={(v) => set('premium', v)} /></Field>
        <Field label="Sum assured"><Money value={f.sum_assured ?? ''} onChange={(v) => set('sum_assured', v)} /></Field>
      </div>
      <div className="row2">
        <Field label="Frequency"><select className="select" value={f.frequency || 'yearly'} onChange={(e) => set('frequency', e.target.value)}>{FREQ.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Renewal date"><input className="input" type="date" value={f.renewal_date || ''} onChange={(e) => set('renewal_date', e.target.value)} /></Field>
      </div>
      <button className="btn block" onClick={() => onSave(f)} disabled={!f.provider}>{initial ? 'Save changes' : 'Add policy'}</button>
      {initial && <HistoryLink entity="policy" id={initial.id} label={initial.provider} block />}
      {initial && <button className="btn danger block" style={{ marginTop: 10 }} onClick={() => onDelete(initial.id)}>Remove policy</button>}
    </Sheet>
  )
}
