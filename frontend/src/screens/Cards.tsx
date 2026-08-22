import { useEffect, useState } from 'react'
import { api } from '../api'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { useAttention } from '../attention'
import { money, dueLabel, fmtDate } from '../format'
import { Sheet, Field, Money } from '../ui'
import { HistoryLink, ModuleScreen } from './Scaffold'
import type { Card, CardPayment } from '../types'

const BANKS = ['HDFC', 'ICICI', 'SBI', 'Axis', 'Amex', 'Kotak', 'IDFC', 'Yes Bank']

const GRADIENTS = [
  'linear-gradient(135deg,#5b3df5,#8b5cf6)',
  'linear-gradient(135deg,#0ea5e9,#2563eb)',
  'linear-gradient(135deg,#ec4899,#be185d)',
  'linear-gradient(135deg,#10b981,#0f766e)',
  'linear-gradient(135deg,#f59e0b,#b45309)',
  'linear-gradient(135deg,#334155,#0f172a)',
  'linear-gradient(135deg,#7c3aed,#4338ca)',
]
function gradientFor(bank: string): string {
  let h = 0
  for (const ch of bank || 'card') h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return GRADIENTS[h % GRADIENTS.length]
}

const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'], v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}

function nextDueISO(day: number): string {
  const t = new Date()
  let y = t.getFullYear(), m = t.getMonth()
  if (day < t.getDate()) { m++; if (m > 11) { m = 0; y++ } }
  const last = new Date(y, m + 1, 0).getDate()
  const d = Math.min(day, last)
  return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

// The credit-card visual. In the list (onEdit/onPay set) it shows the monthly
// paid/unpaid status + a Mark-as-paid button; as a form preview it's compact.
function CardFace({ card, compact, onEdit, onPay }: {
  card: Partial<Card>; compact?: boolean; onEdit?: () => void; onPay?: () => void
}) {
  const dl = card.days_until != null ? dueLabel(card.days_until) : null
  const paid = !!card.paid_this_month
  return (
    <div className={`cc${compact ? ' compact' : ''}`} style={{ background: gradientFor(card.bank || '') }} onClick={onEdit}>
      <div className="cc-glow" />
      <div className="cc-top">
        <div className="cc-bank">{card.bank || 'Card issuer'}</div>
        <div className="cc-chip" />
      </div>
      <div className="cc-num">•••• •••• •••• {card.last4 || '••••'}</div>
      <div className="cc-bottom">
        <div>
          <div className="cc-label">{paid ? 'Next due' : 'Payment due'}</div>
          <div className="cc-val">{card.due_day ? (card.next_due_fmt || `${ordinal(card.due_day)} monthly`) : '—'}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          {card.credit_limit ? <><div className="cc-label">Limit</div><div className="cc-val">{money(card.credit_limit, true)}</div></> : null}
        </div>
      </div>
      {dl && <span className={`pill ${dl.tone} cc-pill`}>{dl.text}</span>}
      {onPay && (
        <div className="cc-pay">
          {paid ? (
            <span className="cc-status"><span className="dot" style={{ background: '#4ade80' }} /> Paid{card.paid_date ? ` · ${fmtDate(card.paid_date)}` : ''}</span>
          ) : (
            <>
              <span className="cc-status"><span className="dot" style={{ background: '#fbbf24' }} /> Unpaid this month</span>
              <button className="cc-paybtn" onClick={(e) => { e.stopPropagation(); onPay() }}>Mark as paid</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function Cards() {
  const { items, loading, refresh, create, update, remove, error } = useResource<Card>('/api/cards')
  const toast = useToast()
  const { refresh: refreshAttn } = useAttention()
  const [edit, setEdit] = useState<Card | null | 'new'>(null)
  const reload = () => { refresh(); refreshAttn() }

  async function save(body: Partial<Card>) {
    if (edit === 'new') { await create(body); toast('Card added · bill reminder set') }
    else if (edit) { await update(edit.id, body); toast('Card updated') }
    refreshAttn(); setEdit(null)
  }
  async function del(id: number) { await remove(id); refreshAttn(); toast('Card removed'); setEdit(null) }
  async function pay(id: number) { await api(`/api/cards/${id}/pay`, { method: 'POST', body: {} }); reload(); toast('Marked paid ✓') }

  return (
    <ModuleScreen mod="cards" sub="Unpaid bills first" loading={loading} empty={items.length === 0} onAdd={() => setEdit('new')} error={error} onRetry={reload} onRefresh={refresh}>
      {items.map((c) => (
        <CardFace key={c.id} card={c} onEdit={() => setEdit(c)} onPay={() => pay(c.id)} />
      ))}
      {edit && <CardForm initial={edit === 'new' ? null : edit} onSave={save} onDelete={del} onChanged={reload} onClose={() => setEdit(null)} />}
    </ModuleScreen>
  )
}

function CardForm({ initial, onSave, onDelete, onChanged, onClose }: {
  initial: Card | null; onSave: (b: Partial<Card>) => void; onDelete: (id: number) => void; onChanged: () => void; onClose: () => void
}) {
  const toast = useToast()
  const [bank, setBank] = useState(initial?.bank || '')
  const [last4, setLast4] = useState(initial?.last4 || '')
  const [limit, setLimit] = useState<string>(initial?.credit_limit != null ? String(initial.credit_limit) : '')
  const [day, setDay] = useState<number | undefined>(initial?.due_day || initial?.billing_day || undefined)
  const [history, setHistory] = useState<CardPayment[] | null>(null)

  const loadHistory = () => {
    if (!initial) return
    api<{ items: CardPayment[] }>(`/api/cards/${initial.id}/payments`).then((d) => setHistory(d.items)).catch(() => setHistory([]))
  }
  useEffect(() => { loadHistory() }, [initial?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  async function unpay(period: string) {
    await api(`/api/cards/${initial!.id}/unpay`, { method: 'POST', body: { period } })
    loadHistory(); onChanged(); toast('Payment undone')
  }

  const valid = bank.trim() && day
  function submit() {
    if (!day) return
    onSave({ bank: bank.trim(), last4, credit_limit: Number(limit) || undefined, billing_day: day, due_date: nextDueISO(day) })
  }

  return (
    <Sheet title={initial ? 'Edit card' : 'Add credit card'} onClose={onClose}>
      <Field label="Bank / issuer">
        <input className="input" value={bank} onChange={(e) => setBank(e.target.value)} placeholder="HDFC Millennia" />
      </Field>
      <div className="chips" style={{ marginTop: -6, marginBottom: 14 }}>
        {BANKS.map((b) => (
          <button key={b} type="button" className={`chip ${bank === b ? 'on' : ''}`} onClick={() => setBank(b)}>{b}</button>
        ))}
      </div>

      <div className="row2">
        <Field label="Last 4 digits">
          <input className="input" inputMode="numeric" maxLength={4} value={last4}
            onChange={(e) => setLast4(e.target.value.replace(/\D/g, ''))} placeholder="4321" />
        </Field>
        <Field label="Credit limit"><Money value={limit} onChange={setLimit} /></Field>
      </div>

      <Field label={day ? `Payment due — ${ordinal(day)} of every month` : 'Payment due day'}>
        <div className="dayscroll">
          {Array.from({ length: 31 }, (_, i) => i + 1).map((d) => (
            <button key={d} type="button" className={day === d ? 'on' : ''} onClick={() => setDay(d)}>{d}</button>
          ))}
        </div>
      </Field>

      {/* payment history */}
      {initial && (
        <>
          <div className="section-title" style={{ marginTop: 8 }}>Payment history</div>
          {history === null ? <p className="muted" style={{ fontSize: 13 }}>Loading…</p>
            : history.length === 0 ? <p className="muted" style={{ fontSize: 13, marginBottom: 14 }}>No payments recorded yet.</p>
              : (
                <div className="list" style={{ gap: 8, marginBottom: 14 }}>
                  {history.map((h) => (
                    <div key={h.period} className="card" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 13px' }}>
                      <span className="dot" style={{ width: 8, height: 8, borderRadius: 999, background: 'var(--ok)', display: 'inline-block' }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 700, fontSize: 14 }}>{h.period_label}</div>
                        <div className="sub" style={{ fontSize: 12 }}>Paid {fmtDate(h.paid_date)}{h.amount ? ` · ${money(h.amount)}` : ''}</div>
                      </div>
                      <button className="btn ghost sm" onClick={() => unpay(h.period)}>Undo</button>
                    </div>
                  ))}
                </div>
              )}
        </>
      )}

      {!valid && (
        <p className="form-hint warn">
          {!bank.trim() ? 'Enter the bank name' : 'Enter the statement/billing day'}
        </p>
      )}
      <button className="btn block" onClick={submit} disabled={!valid}>{initial ? 'Save changes' : 'Add card'}</button>
      {initial && <HistoryLink entity="card" id={initial.id} label={`${initial.bank} ••${initial.last4}`} block />}
      {initial && <button className="btn danger block" style={{ marginTop: 10 }} onClick={() => onDelete(initial.id)}>Remove card</button>}
    </Sheet>
  )
}
