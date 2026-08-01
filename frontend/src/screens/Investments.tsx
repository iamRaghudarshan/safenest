import { useState } from 'react'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { money, fmtDate } from '../format'
import { Sheet, Field, Money } from '../ui'
import { IcTrend } from '../icons'
import { HistoryLink, ModuleScreen } from './Scaffold'
import type { Investment } from '../types'

const TYPES = ['Stocks', 'Mutual Fund', 'SIP', 'FD', 'PPF', 'Gold', 'Crypto', 'Bonds']

export default function Investments() {
  const { items, loading, refresh, create, update, remove, error, reload} = useResource<Investment>('/api/investments')
  const toast = useToast()
  const [edit, setEdit] = useState<Investment | null | 'new'>(null)

  const totalVal = items.reduce((s, i) => s + Number(i.current_value || 0), 0)
  const totalCost = items.reduce((s, i) => s + Number(i.invested_amount || 0), 0)

  async function save(body: Partial<Investment>) {
    if (edit === 'new') { await create(body); toast('Investment added') }
    else if (edit) { await update(edit.id, body); toast('Investment updated') }
    setEdit(null)
  }
  async function del(id: number) { await remove(id); toast('Investment removed'); setEdit(null) }

  return (
    <ModuleScreen mod="investments" sub={`Portfolio ${money(totalVal, true)}`} loading={loading} empty={items.length === 0} onAdd={() => setEdit('new')} error={error} onRetry={reload} onRefresh={refresh}>
      {items.length > 0 && (
        <div className="card" style={{ background: 'linear-gradient(135deg,#059669,#10b981)', color: '#fff', marginBottom: 4 }}>
          <div style={{ fontSize: 13, opacity: 0.9 }}>Current value</div>
          <div className="tabnum" style={{ fontSize: 28, fontWeight: 800 }}>{money(totalVal)}</div>
          <div style={{ fontSize: 13, marginTop: 4 }}>Invested {money(totalCost, true)} · {totalCost ? (((totalVal - totalCost) / totalCost) * 100).toFixed(1) : 0}% return</div>
        </div>
      )}
      {items.map((i) => {
        const cost = Number(i.invested_amount || 0)
        const gain = Number(i.current_value || 0) - cost
        const pct = cost ? (gain / cost) * 100 : 0
        const up = gain >= 0
        const gc = up ? 'var(--ok)' : 'var(--danger)'
        return (
          <button key={i.id} className="mcard" onClick={() => setEdit(i)}>
            <div className="mcard-head">
              <div className="mcard-ic" style={{ background: 'var(--c-investments)' }}><IcTrend /></div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mcard-title">{i.name}</div>
                <div className="mcard-sub">{i.invest_type}{i.broker ? ` · ${i.broker}` : ''}</div>
              </div>
              <span className="pill" style={{ background: `color-mix(in srgb, ${gc} 15%, transparent)`, color: gc }}>{up ? '▲' : '▼'} {Math.abs(pct).toFixed(1)}%</span>
            </div>
            <div className="mcard-row">
              <div>
                <div className="mcard-label">Current value</div>
                <div className="mcard-big">{money(i.current_value)}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="mcard-label">Invested</div>
                <div className="mcard-val">{money(cost)}</div>
              </div>
            </div>
            <div className="mcard-foot">
              <span style={{ color: gc, fontWeight: 700 }}>{up ? '+' : '−'}{money(Math.abs(gain))} {up ? 'gain' : 'loss'}</span>
              {i.maturity_date && <span>Matures {fmtDate(i.maturity_date)}</span>}
            </div>
          </button>
        )
      })}
      {edit && <InvForm initial={edit === 'new' ? null : edit} onSave={save} onDelete={del} onClose={() => setEdit(null)} />}
    </ModuleScreen>
  )
}

function InvForm({ initial, onSave, onDelete, onClose }: { initial: Investment | null; onSave: (b: Partial<Investment>) => void; onDelete: (id: number) => void; onClose: () => void }) {
  const [f, setF] = useState<Partial<Investment>>(initial || { invest_type: 'Mutual Fund' })
  const set = (k: keyof Investment, v: unknown) => setF((p) => ({ ...p, [k]: v }))
  return (
    <Sheet title={initial ? 'Edit investment' : 'Add investment'} onClose={onClose}>
      <Field label="Name"><input className="input" value={f.name || ''} onChange={(e) => set('name', e.target.value)} placeholder="Nifty 50 Index Fund" /></Field>
      <div className="row2">
        <Field label="Type"><select className="select" value={f.invest_type || 'Mutual Fund'} onChange={(e) => set('invest_type', e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Broker / platform"><input className="input" value={f.broker || ''} onChange={(e) => set('broker', e.target.value)} placeholder="Zerodha" /></Field>
      </div>
      <div className="row2">
        <Field label="Invested amount"><Money value={f.invested_amount ?? ''} onChange={(v) => set('invested_amount', v)} /></Field>
        <Field label="Current value"><Money value={f.current_value ?? ''} onChange={(v) => set('current_value', v)} /></Field>
      </div>
      <div className="row2">
        <Field label="Units"><input className="input" inputMode="decimal" value={f.units ?? ''} onChange={(e) => set('units', e.target.value)} /></Field>
        <Field label="Maturity date"><input className="input" type="date" value={f.maturity_date || ''} onChange={(e) => set('maturity_date', e.target.value)} /></Field>
      </div>
      <button className="btn block" onClick={() => onSave(f)} disabled={!f.name}>{initial ? 'Save changes' : 'Add investment'}</button>
      {initial && <HistoryLink entity="investment" id={initial.id} label={initial.name} block />}
      {initial && <button className="btn danger block" style={{ marginTop: 10 }} onClick={() => onDelete(initial.id)}>Remove investment</button>}
    </Sheet>
  )
}
