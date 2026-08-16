// Admin support-ticket console: list/filter tickets, open a thread, reply (emails
// the customer), and set status/priority. Customers raise tickets from the website.
import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from '../api'
import { useToast } from '../toast'
import { TopBar, Sheet, Field } from '../ui'

interface Msg { id: number; author: string; author_name: string; body: string; at: string }
interface Tkt {
  id: number; subject: string; status: string; priority: string
  name: string; email: string; source: string; created_at: string; updated_at: string; messages?: Msg[]
}

const STC: Record<string, string> = { open: 'var(--ok)', pending: 'var(--warn)', closed: 'var(--ink-faint)' }

export default function Tickets() {
  const toast = useToast()
  const [data, setData] = useState<{ tickets: Tkt[]; open: number } | null>(null)
  const [filter, setFilter] = useState('')
  const [open, setOpen] = useState<number | null>(null)

  const load = useCallback(async () => {
    try { setData(await api<{ tickets: Tkt[]; open: number }>(`/api/admin/tickets${filter ? `?status=${filter}` : ''}`)) }
    catch (e) { toast(errorMessage(e, 'Could not load tickets')) }
  }, [toast, filter])
  useEffect(() => { load() }, [load])

  return (
    <div className="screen">
      <TopBar title="Support tickets" sub={data ? `${data.open} open` : ''} />
      <div className="lic-presets" style={{ marginBottom: 12 }}>
        {([['', 'All'], ['open', 'Open'], ['pending', 'Pending'], ['closed', 'Closed']] as const).map(([v, l]) => (
          <button key={v} type="button" className={`chip${filter === v ? ' on' : ''}`} onClick={() => setFilter(v)}>{l}</button>
        ))}
      </div>
      {!data ? <div className="spinner" />
        : data.tickets.length === 0 ? <p className="muted" style={{ textAlign: 'center', marginTop: 30 }}>No tickets here.</p>
          : data.tickets.map((t) => (
            <button key={t.id} type="button" className="req-card" style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }} onClick={() => setOpen(t.id)}>
              <span className="lic-card-av" style={{ background: STC[t.status] || 'var(--brand)' }}>{(t.name || '?').trim().charAt(0).toUpperCase()}</span>
              <div className="req-body">
                <div className="req-top"><b>{t.subject}</b><span className="muted">{t.updated_at}</span></div>
                <div className="req-mail">{t.name} · {t.email}{t.source === 'web' ? ' · web' : ''}{t.priority === 'high' ? ' · ⚠ high' : ''}</div>
              </div>
              <span className="tag" style={{ color: STC[t.status], borderColor: STC[t.status] }}>{t.status}</span>
            </button>
          ))}
      {open != null && <TicketSheet id={open} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  )
}

function TicketSheet({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const [t, setT] = useState<Tkt | null>(null)
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setT(await api<Tkt>(`/api/admin/tickets/${id}`)) }
    catch (e) { toast(errorMessage(e, 'Could not load the ticket')) }
  }, [id, toast])
  useEffect(() => { load() }, [load])

  async function sendReply() {
    if (!reply.trim()) return
    setBusy(true)
    try {
      await api(`/api/admin/tickets/${id}/reply`, { method: 'POST', body: { body: reply.trim(), status: 'pending' } })
      setReply(''); toast('Reply sent — the customer is emailed'); await load(); onChanged()
    } catch (e) { toast(errorMessage(e, 'Could not send')) } finally { setBusy(false) }
  }
  async function setStatus(status: string) {
    setBusy(true)
    try { await api(`/api/admin/tickets/${id}/status`, { method: 'POST', body: { status } }); await load(); onChanged() }
    catch (e) { toast(errorMessage(e, 'Could not update')) } finally { setBusy(false) }
  }

  return (
    <Sheet title={t ? t.subject : 'Ticket'} onClose={onClose}>
      {!t ? <div className="spinner" /> : (
        <>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            {t.name} · {t.email} · <b style={{ color: STC[t.status] }}>{t.status}</b> · {t.priority}
          </p>
          <div className="ticket-thread">
            {(t.messages || []).map((m) => (
              <div key={m.id} className={`tmsg ${m.author}`}>
                <div className="tmsg-h">{m.author_name} · {m.at}</div>
                <div className="tmsg-b">{m.body}</div>
              </div>
            ))}
          </div>
          <Field label="Reply (emails the customer)">
            <textarea className="input" rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Type your reply…" />
          </Field>
          <button className="btn primary block" disabled={busy || !reply.trim()} onClick={sendReply}>{busy ? 'Sending…' : 'Send reply'}</button>
          <div className="lic-presets" style={{ marginTop: 10 }}>
            <button type="button" className="chip" disabled={busy} onClick={() => setStatus('open')}>Reopen</button>
            <button type="button" className="chip" disabled={busy} onClick={() => setStatus('pending')}>Pending</button>
            <button type="button" className="chip" disabled={busy} onClick={() => setStatus('closed')}>Close</button>
          </div>
        </>
      )}
    </Sheet>
  )
}
