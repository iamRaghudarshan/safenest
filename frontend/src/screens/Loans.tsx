import { useEffect, useState } from 'react'
import { api } from '../api'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { useAttention } from '../attention'
import { money, fmtDate, dueLabel } from '../format'
import { Sheet, Field, Money, Segment } from '../ui'
import { IcLoans } from '../icons'
import { HistoryLink, ModuleScreen } from './Scaffold'
import type { Loan, CardPayment } from '../types'

const TYPES = ['Home', 'Personal', 'Car', 'Education', 'Gold', 'Business']

export default function Loans() {
  const { items, loading, refresh, create, update, remove, error } = useResource<Loan>('/api/loans')
  const toast = useToast()
  const { refresh: refreshAttn } = useAttention()
  const [edit, setEdit] = useState<Loan | null | 'new'>(null)
  const reload = () => { refresh(); refreshAttn() }

  async function save(body: Partial<Loan>) {
    if (edit === 'new') { await create(body); toast('Loan added · reminder scheduled') }
    else if (edit) { await update(edit.id, body); toast('Loan updated') }
    refreshAttn(); setEdit(null)
  }
  async function del(id: number) { await remove(id); refreshAttn(); toast('Loan removed'); setEdit(null) }
  async function pay(id: number) { await api(`/api/loans/${id}/pay`, { method: 'POST', body: {} }); reload(); toast('EMI marked paid ✓') }

  return (
    <ModuleScreen mod="loans" sub="Unpaid EMIs first" loading={loading} empty={items.length === 0} onAdd={() => setEdit('new')} error={error} onRetry={reload} onRefresh={refresh}>
      {items.map((l) => {
        const dl = dueLabel(l.days_until ?? null)
        const pct = l.principal ? Math.max(0, Math.min(100, Math.round((l.principal - l.outstanding) / l.principal * 100))) : 0
        const paid = !!l.paid_this_month
        return (
          <div key={l.id} className="mcard" role="button" onClick={() => setEdit(l)}>
            <div className="mcard-head">
              <div className="mcard-ic" style={{ background: 'var(--c-loans)' }}><IcLoans /></div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mcard-title">{l.lender}</div>
                <div className="mcard-sub">{l.loan_type} loan · {l.interest_rate}% p.a.</div>
              </div>
              {l.days_until != null && <span className={`pill ${dl.tone}`}>{dl.text}</span>}
            </div>
            <div className="mcard-row">
              <div>
                <div className="mcard-label">Outstanding</div>
                <div className="mcard-big">{money(l.outstanding)}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="mcard-label">EMI</div>
                <div className="mcard-val">{money(l.emi)}</div>
              </div>
            </div>
            <div className="progress"><i style={{ width: `${pct}%`, background: 'var(--c-loans)' }} /></div>
            <div className="mcard-foot">
              <span>{pct}% paid off</span>
              <span>{paid ? 'Next EMI' : 'EMI due'} {fmtDate(l.next_emi || l.next_due_date)}</span>
            </div>
            <div className="mcard-pay">
              {paid ? (
                <span className="status" style={{ color: 'var(--ok)' }}><span className="dot" style={{ background: 'var(--ok)' }} /> EMI paid{l.paid_date ? ` · ${l.paid_date}` : ''}</span>
              ) : (
                <>
                  <span className="status" style={{ color: 'var(--warn)' }}><span className="dot" style={{ background: 'var(--warn)' }} /> EMI unpaid this month</span>
                  <button className="paybtn" onClick={(e) => { e.stopPropagation(); pay(l.id) }}>Mark as paid</button>
                </>
              )}
            </div>
          </div>
        )
      })}
      {edit && <LoanForm initial={edit === 'new' ? null : edit} onSave={save} onDelete={del} onChanged={reload} onClose={() => setEdit(null)} />}
    </ModuleScreen>
  )
}

function LoanForm({ initial, onSave, onDelete, onChanged, onClose }: { initial: Loan | null; onSave: (b: Partial<Loan>) => void; onDelete: (id: number) => void; onChanged: () => void; onClose: () => void }) {
  const toast = useToast()
  const [f, setF] = useState<Partial<Loan>>(initial || { loan_type: 'Home', status: 'active' })
  const set = (k: keyof Loan, v: unknown) => setF((p) => ({ ...p, [k]: v }))
  const [history, setHistory] = useState<CardPayment[] | null>(null)
  const loadHistory = () => {
    if (!initial) return
    api<{ items: CardPayment[] }>(`/api/loans/${initial.id}/payments`).then((d) => setHistory(d.items)).catch(() => setHistory([]))
  }
  useEffect(() => { loadHistory() }, [initial?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  async function unpay(period: string) {
    await api(`/api/loans/${initial!.id}/unpay`, { method: 'POST', body: { period } })
    loadHistory(); onChanged(); toast('EMI payment undone')
  }
  return (
    <Sheet title={initial ? 'Edit loan' : 'Add loan'} onClose={onClose}>
      <Field label="Lender"><input className="input" value={f.lender || ''} onChange={(e) => set('lender', e.target.value)} placeholder="HDFC Bank" /></Field>
      <Field label="Loan type">
        <select className="select" value={f.loan_type || 'Home'} onChange={(e) => set('loan_type', e.target.value)}>
          {TYPES.map((t) => <option key={t}>{t}</option>)}
        </select>
      </Field>
      <div className="row2">
        <Field label="Principal"><Money value={f.principal ?? ''} onChange={(v) => set('principal', v)} /></Field>
        <Field label="Outstanding"><Money value={f.outstanding ?? ''} onChange={(v) => set('outstanding', v)} /></Field>
      </div>
      <div className="row2">
        <Field label="EMI"><Money value={f.emi ?? ''} onChange={(v) => set('emi', v)} /></Field>
        <Field label="Interest %"><input className="input" inputMode="decimal" value={f.interest_rate ?? ''} onChange={(e) => set('interest_rate', e.target.value)} /></Field>
      </div>
      <div className="row2">
        <Field label="Tenure (months)"><input className="input" inputMode="numeric" value={f.tenure_months ?? ''} onChange={(e) => set('tenure_months', e.target.value)} /></Field>
        <Field label="Next EMI date"><input className="input" type="date" value={f.next_due_date || ''} onChange={(e) => set('next_due_date', e.target.value)} /></Field>
      </div>
      <Field label="Status">
        <Segment value={f.status || 'active'} onChange={(v) => set('status', v)}
          options={[{ value: 'active', label: 'Active' }, { value: 'closed', label: 'Closed' }]} />
      </Field>
      {initial && (
        <>
          <div className="section-title" style={{ marginTop: 8 }}>EMI payment history</div>
          {history === null ? <p className="muted" style={{ fontSize: 13 }}>Loading…</p>
            : history.length === 0 ? <p className="muted" style={{ fontSize: 13, marginBottom: 14 }}>No EMIs recorded yet.</p>
              : (
                <div className="list" style={{ gap: 8, marginBottom: 14 }}>
                  {history.map((h) => (
                    <div key={h.period} className="card" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 13px' }}>
                      <span style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--ok)', display: 'inline-block' }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 700, fontSize: 14 }}>{h.period_label}</div>
                        <div className="sub" style={{ fontSize: 12 }}>Paid {h.paid_date}{h.amount ? ` · ${money(h.amount)}` : ''}</div>
                      </div>
                      <button className="btn ghost sm" onClick={() => unpay(h.period)}>Undo</button>
                    </div>
                  ))}
                </div>
              )}
        </>
      )}
      <button className="btn block" onClick={() => onSave(f)} disabled={!f.lender}>{initial ? 'Save changes' : 'Add loan'}</button>
      {initial && <HistoryLink entity="loan" id={initial.id} label={initial.lender} block />}
      {initial && <button className="btn danger block" style={{ marginTop: 10 }} onClick={() => onDelete(initial.id)}>Remove loan</button>}
    </Sheet>
  )
}
