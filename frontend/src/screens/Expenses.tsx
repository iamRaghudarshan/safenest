import { useEffect, useState } from 'react'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { useNav } from '../nav'
import { fmtDate, istDayISO, money, todayISO } from '../format'
import { Sheet, Field, Money, Segment } from '../ui'
import { HistoryLink, ModuleScreen } from './Scaffold'
import type { Expense } from '../types'

const EXP_CATS = ['Food', 'Groceries', 'Transport', 'Shopping', 'Bills', 'Health', 'Entertainment', 'Rent', 'Education', 'Others']
const INC_CATS = ['Salary', 'Business', 'Freelance', 'Interest', 'Dividend', 'Rental', 'Gift', 'Others']
const METHODS = ['UPI', 'Card', 'Cash', 'Netbanking', 'Wallet']

// Category → emoji + colour (matched by keyword so "Food & Dining" still maps to Food).
const CATS = [
  { k: 'food', e: '🍔', c: '#f59e0b' }, { k: 'groc', e: '🛒', c: '#10b981' },
  { k: 'transport', e: '🚗', c: '#3b82f6' }, { k: 'shop', e: '🛍️', c: '#ec4899' },
  { k: 'bill', e: '📄', c: '#8b5cf6' }, { k: 'health', e: '💊', c: '#ef4444' },
  { k: 'entertain', e: '🎬', c: '#f43f5e' }, { k: 'educat', e: '📚', c: '#14b8a6' },
  { k: 'salary', e: '💰', c: '#10b981' }, { k: 'business', e: '💼', c: '#0ea5e9' },
  { k: 'freelance', e: '💻', c: '#8b5cf6' }, { k: 'interest', e: '🏦', c: '#10b981' },
  { k: 'dividend', e: '📈', c: '#10b981' }, { k: 'rental', e: '🏢', c: '#0ea5e9' },
  { k: 'rent', e: '🏠', c: '#6366f1' }, { k: 'gift', e: '🎁', c: '#ec4899' },
]
function catMeta(cat: string) {
  const s = (cat || '').toLowerCase()
  return CATS.find((x) => s.includes(x.k)) || { e: '💸', c: '#64748b' }
}

function dateHeader(iso: string | null): string {
  if (!iso) return 'Undated'
  if (iso === todayISO()) return 'Today'
  if (iso === istDayISO(-1)) return 'Yesterday'
  return fmtDate(iso)
}

export default function Expenses() {
  const { items, extra, loading, refresh, create, remove, error, reload} = useResource<Expense>('/api/expenses')
  const toast = useToast()
  const { takeIntent } = useNav()
  const [open, setOpen] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)
  const totals = (extra.totals as { income: number; expense: number }) || { income: 0, expense: 0 }

  useEffect(() => { if (takeIntent() === 'add') setOpen(true) }, [takeIntent])

  async function save(body: Partial<Expense>) { await create(body); toast('Saved'); setOpen(false) }

  // group transactions by day (items already sorted by txn_date desc)
  const groups: { date: string | null; items: Expense[] }[] = []
  for (const e of items) {
    const last = groups[groups.length - 1]
    if (last && last.date === e.txn_date) last.items.push(e)
    else groups.push({ date: e.txn_date, items: [e] })
  }

  return (
    <ModuleScreen mod="expenses" sub="This month" loading={loading} empty={items.length === 0} onAdd={() => setOpen(true)} error={error} onRetry={reload} onRefresh={refresh}>
      {/* month summary */}
      <div className="card" style={{ display: 'flex', alignItems: 'center', padding: 16 }}>
        <div style={{ flex: 1 }}>
          <div className="mcard-label">Income</div>
          <div className="tabnum" style={{ fontSize: 21, fontWeight: 800, color: 'var(--ok)' }}>{money(totals.income, true)}</div>
        </div>
        <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)', margin: '2px 0' }} />
        <div style={{ flex: 1, paddingLeft: 16 }}>
          <div className="mcard-label">Spent</div>
          <div className="tabnum" style={{ fontSize: 21, fontWeight: 800, color: 'var(--danger)' }}>{money(totals.expense, true)}</div>
        </div>
      </div>

      {/* date-grouped transactions */}
      {groups.map((g) => (
        <div key={g.date || 'undated'}>
          <div className="txn-date-head">{dateHeader(g.date)}</div>
          <div className="list" style={{ gap: 8 }}>
            {g.items.map((e) => {
              const m = catMeta(e.category)
              const inc = e.kind === 'income'
              return (
                <div key={e.id}>
                  <button className="txn" onClick={() => setOpenId(openId === e.id ? null : e.id)}>
                    <div className="txn-ic" style={{ background: `color-mix(in srgb, ${m.c} 18%, transparent)` }}>{m.e}</div>
                    <div className="txn-main">
                      <div className="txn-cat">{e.category}</div>
                      <div className="txn-sub">{e.method || '—'}{e.note ? ` · ${e.note}` : ''}</div>
                    </div>
                    <div className="txn-amt" style={{ color: inc ? 'var(--ok)' : 'var(--ink)' }}>{inc ? '+' : '−'}{money(e.amount)}</div>
                  </button>
                  {openId === e.id && (
                    <div className="swipe-actions">
                      <HistoryLink entity="expense" id={e.id} label={`${e.category} ${e.amount}`} />
                      <button className="btn danger sm" style={{ flex: 1 }} onClick={() => { remove(e.id); toast('Deleted'); setOpenId(null) }}>Delete</button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
      {open && <ExpForm onSave={save} onClose={() => setOpen(false)} />}
    </ModuleScreen>
  )
}

function ExpForm({ onSave, onClose }: { onSave: (b: Partial<Expense>) => void; onClose: () => void }) {
  const [kind, setKind] = useState<'expense' | 'income'>('expense')
  const [amount, setAmount] = useState('')
  const [category, setCategory] = useState('Food')
  const [method, setMethod] = useState('UPI')
  const [txn_date, setDate] = useState(todayISO())
  const [note, setNote] = useState('')
  const cats = kind === 'income' ? INC_CATS : EXP_CATS

  return (
    <Sheet title="Add transaction" onClose={onClose}>
      <Field label="Type">
        <Segment value={kind} onChange={(v) => { setKind(v); setCategory(v === 'income' ? 'Salary' : 'Food') }}
          options={[{ value: 'expense', label: '− Expense' }, { value: 'income', label: '+ Income' }]} />
      </Field>
      <Field label="Amount"><Money value={amount} onChange={setAmount} /></Field>
      <Field label="Category">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {cats.map((c) => (
            <button key={c} type="button" onClick={() => setCategory(c)}
              className="pill" style={{ padding: '9px 12px', fontSize: 13, background: category === c ? 'var(--brand)' : 'var(--bg)', color: category === c ? '#fff' : 'var(--ink-soft)', border: '1.5px solid var(--line)' }}>
              {catMeta(c).e} {c}
            </button>
          ))}
        </div>
      </Field>
      <div className="row2">
        <Field label="Method"><select className="select" value={method} onChange={(e) => setMethod(e.target.value)}>{METHODS.map((m) => <option key={m}>{m}</option>)}</select></Field>
        <Field label="Date"><input className="input" type="date" value={txn_date} onChange={(e) => setDate(e.target.value)} /></Field>
      </div>
      <Field label="Note (optional)"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Lunch with team" /></Field>
      <button className="btn block" onClick={() => onSave({ kind, amount: Number(amount), category, method, txn_date, note })} disabled={!amount || Number(amount) <= 0}>Add {kind}</button>
    </Sheet>
  )
}
