// Admin SMTP settings — how the publisher emails customers (licence keys on
// approval, and announcements/ads via broadcast). The password is stored
// encrypted server-side and never sent back to the browser.
import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from '../api'
import { useToast } from '../toast'
import { TopBar, Field } from '../ui'

interface Mail {
  enabled: boolean; host: string; port: number; username: string
  from_addr: string; from_name: string; security: string; has_password: boolean
}

interface LogItem { id: number; to: string; subject: string; kind: string; status: string; error: string | null; at: string; sent_at: string | null }
const MLOG_C: Record<string, string> = { sent: 'var(--ok)', queued: 'var(--warn)', failed: 'var(--danger)' }

export default function MailSettings() {
  const toast = useToast()
  const [f, setF] = useState<Mail | null>(null)
  const [pw, setPw] = useState('')
  const [testTo, setTestTo] = useState('')
  const [busy, setBusy] = useState('')
  const [log, setLog] = useState<{ queued: number; items: LogItem[] } | null>(null)

  const load = useCallback(async () => {
    try { setF(await api<Mail>('/api/admin/mail')) }
    catch (e) { toast(errorMessage(e, 'Could not load email settings')) }
    try { setLog(await api<{ queued: number; items: LogItem[] }>('/api/admin/mail/log')) } catch { /* not critical */ }
  }, [toast])
  useEffect(() => { load() }, [load])

  const set = (k: keyof Mail, v: unknown) => setF((p) => (p ? { ...p, [k]: v } : p))

  async function save() {
    if (!f) return
    setBusy('save')
    try {
      const body: Record<string, unknown> = { ...f }
      if (pw) body.password = pw
      const next = await api<Mail>('/api/admin/mail', { method: 'PUT', body })
      setF(next); setPw(''); toast('Email settings saved')
    } catch (e) { toast(errorMessage(e, 'Could not save')) } finally { setBusy('') }
  }

  async function test() {
    setBusy('test')
    try {
      const r = await api<{ sent_to: string }>('/api/admin/mail/test', { method: 'POST', body: { to: testTo.trim() } })
      toast(`Test email sent to ${r.sent_to}`)
    } catch (e) { toast(errorMessage(e, 'Test failed — check the settings')) } finally { setBusy('') }
  }

  if (!f) return <div className="screen"><div className="spinner" /></div>

  return (
    <div className="screen">
      <TopBar title="Email (SMTP)" />
      <div style={{ maxWidth: 560, margin: '0 auto' }}>
        <label className="lic-check" style={{ marginBottom: 8 }}>
          <input type="checkbox" checked={f.enabled} onChange={(e) => set('enabled', e.target.checked)} />
          <span>Send emails to customers (licence keys &amp; announcements)</span>
        </label>

        <Field label="SMTP host">
          <input className="input" value={f.host} onChange={(e) => set('host', e.target.value)} placeholder="smtp.gmail.com" autoCapitalize="off" />
        </Field>
        <div className="row2">
          <Field label="Port">
            <input className="input" type="number" value={f.port} onChange={(e) => set('port', Number(e.target.value) || 0)} />
          </Field>
          <Field label="Security">
            <select className="input" value={f.security} onChange={(e) => set('security', e.target.value)}>
              <option value="tls">STARTTLS (587)</option>
              <option value="ssl">SSL/TLS (465)</option>
              <option value="none">None</option>
            </select>
          </Field>
        </div>
        <Field label="Username">
          <input className="input" value={f.username} onChange={(e) => set('username', e.target.value)} placeholder="you@gmail.com" autoComplete="off" autoCapitalize="off" />
        </Field>
        <Field label={f.has_password ? 'Password (leave blank to keep current)' : 'Password'}>
          <input className="input" type="password" value={pw} onChange={(e) => setPw(e.target.value)}
            placeholder={f.has_password ? '••••••••' : 'app password'} autoComplete="new-password" />
        </Field>
        <div className="row2">
          <Field label="From name"><input className="input" value={f.from_name} onChange={(e) => set('from_name', e.target.value)} placeholder="SafeNest" /></Field>
          <Field label="From address"><input className="input" type="email" value={f.from_addr} onChange={(e) => set('from_addr', e.target.value)} placeholder="noreply@yourdomain.com" autoCapitalize="off" /></Field>
        </div>

        <button className="btn block" disabled={busy === 'save'} onClick={save}>{busy === 'save' ? 'Saving…' : 'Save settings'}</button>
        <p className="form-hint">
          For Gmail, use an <b>App Password</b> (Google Account → Security → App passwords),
          not your normal password. The password is stored AES-encrypted on this machine.
        </p>

        <div style={{ marginTop: 22, paddingTop: 16, borderTop: '1px solid var(--line)' }}>
          <Field label="Send a test email to">
            <input className="input" type="email" value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="you@example.com" autoCapitalize="off" />
          </Field>
          <button className="btn ghost block" disabled={busy === 'test'} onClick={test}>{busy === 'test' ? 'Sending…' : 'Send test email'}</button>
          <p className="form-hint">Save your settings first, then send a test to confirm they work.</p>
        </div>

        {log && (
          <div style={{ marginTop: 22, paddingTop: 16, borderTop: '1px solid var(--line)' }}>
            <div className="req-top" style={{ marginBottom: 6 }}>
              <b>Send log</b>
              <button className="btn ghost xs" onClick={load}>Refresh</button>
            </div>
            <p className="form-hint" style={{ marginTop: 0 }}>
              {log.queued > 0 ? `${log.queued} email${log.queued === 1 ? '' : 's'} waiting in the queue — sent one at a time.` : 'Queue is empty. Recent sends below.'}
            </p>
            {log.items.length === 0 ? <p className="muted">Nothing sent yet.</p> : log.items.map((m) => (
              <div key={m.id} className="mlog">
                <span className="mlog-dot" style={{ background: MLOG_C[m.status] || 'var(--ink-faint)' }} />
                <div className="mlog-main">
                  <div className="ellipsis"><b>{m.subject}</b></div>
                  <div className="mlog-sub ellipsis">
                    {m.to} · {m.status}{m.sent_at ? ` · ${m.sent_at}` : ` · ${m.at}`}{m.error ? ` · ${m.error}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
