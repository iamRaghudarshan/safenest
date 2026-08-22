// The publisher's admin console: analytics + every admin feature + reports in one
// place. KPIs drill into filtered tables with row actions; quick-actions jump to
// each feature; report cards summarise licences, versions, activity and releases.
// Charts are inline CSS/SVG — no library, CSP-safe (script-src 'self').
import { useCallback, useEffect, useState } from 'react'
import { fmtDateTime } from '../format'
import { api, errorMessage } from '../api'
import { useToast } from '../toast'
import { useNav } from '../nav'
import { useAuth } from '../auth'
import type { Licence, LicenceList, ActivityRow, ActivitySummary } from '../types'

interface Req { id: number; name: string; email: string; message: string | null; platform: string | null; status: string; created_at: string | null }
interface Usr { id: number; name: string; email: string; role: string; status: string }
interface Rel { id: number; version: string; is_current: boolean; published_at: string | null }
interface Site { total: number; today: number; days: { day: string; visits: number }[] }

function Donut({ segs, total }: { segs: { label: string; value: number; color: string }[]; total: number }) {
  const sum = segs.reduce((a, s) => a + s.value, 0) || 1
  let acc = 0
  const stops = segs.filter((s) => s.value > 0).map((s) => {
    const start = (acc / sum) * 100; acc += s.value; return `${s.color} ${start}% ${(acc / sum) * 100}%`
  }).join(', ')
  return (
    <div className="donut-wrap">
      <div className="donut" style={{ background: `conic-gradient(${stops || 'var(--line) 0 100%'})` }}>
        <div className="donut-hole"><span className="donut-n">{total}</span><span className="donut-l">licences</span></div>
      </div>
      <div className="legend">
        {segs.map((s) => (
          <div className="leg" key={s.label}><span className="leg-dot" style={{ background: s.color }} /><span className="leg-l">{s.label}</span><b>{s.value}</b></div>
        ))}
      </div>
    </div>
  )
}

function Bars({ rows }: { rows: { label: string; value: number; color: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="barrow" key={r.label}>
          <span className="bar-l">{r.label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(r.value / max) * 100}%`, background: r.color }} /></span>
          <span className="bar-v">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

const STATE_LABEL: Record<string, string> = { ok: 'Active', expiring: 'Expiring', grace: 'Grace', expired: 'Expired', revoked: 'Withdrawn', suspended: 'Suspended', invalid: 'Invalid', missing: 'No licence' }
const STATE_COLOR: Record<string, string> = { ok: 'var(--ok)', expiring: 'var(--warn)', grace: '#f59e0b', expired: 'var(--danger)', revoked: '#64748b', suspended: '#a855f7', invalid: 'var(--danger)', missing: 'var(--ink-faint)' }
const TONE: Record<string, string> = { ok: 'var(--ok)', info: 'var(--brand)', warn: 'var(--warn)', danger: 'var(--danger)' }
const PALETTE = ['var(--brand)', '#7b3ff2', '#0ea5e9', '#10b981', '#f59e0b']

type Drill = 'customers' | 'active' | 'renewals' | 'lapsed' | 'requests' | 'users'
const DRILL_TITLE: Record<Drill, string> = { customers: 'All customers', active: 'Active licences', renewals: 'Renewals due (≤30 days)', lapsed: 'Lapsed licences', requests: 'Licence requests', users: 'User accounts' }

export default function AdminDashboard() {
  const toast = useToast()
  const { go } = useNav()
  const { user } = useAuth()
  const [lic, setLic] = useState<Licence[] | null>(null)
  const [reqs, setReqs] = useState<Req[]>([])
  const [users, setUsers] = useState<Usr[]>([])
  const [sum, setSum] = useState<ActivitySummary | null>(null)
  const [recent, setRecent] = useState<ActivityRow[]>([])
  const [rels, setRels] = useState<{ releases: Rel[]; running: string; customers: number } | null>(null)
  const [site, setSite] = useState<Site | null>(null)
  const [ticketsOpen, setTicketsOpen] = useState<number | null>(null)
  const [drill, setDrill] = useState<Drill>('customers')
  const [busy, setBusy] = useState(0)

  const load = useCallback(async () => {
    try {
      const [l, r, u, sm, ac, rl, st, tk] = await Promise.all([
        api<LicenceList>('/api/licences'),
        api<{ requests: Req[] }>('/api/licence-requests').catch(() => ({ requests: [] as Req[] })),
        api<{ users: Usr[] }>('/api/admin/users').catch(() => ({ users: [] as Usr[] })),
        api<ActivitySummary>('/api/activity/summary?days=30').catch(() => null),
        api<{ items: ActivityRow[] }>('/api/activity?limit=8').catch(() => ({ items: [] as ActivityRow[] })),
        api<{ releases: Rel[]; running: string; customers: number }>('/api/releases').catch(() => null),
        api<Site>('/api/admin/site-stats').catch(() => null),
        api<{ open: number }>('/api/admin/tickets?status=open').catch(() => ({ open: 0 })),
      ])
      setLic(l.licences); setReqs(r.requests); setUsers(u.users); setSum(sm); setRecent(ac.items.slice(0, 8)); setRels(rl)
      setSite(st); setTicketsOpen(tk.open)
    } catch (e) { toast(errorMessage(e, 'Could not load the console')) }
  }, [toast])
  useEffect(() => { load() }, [load])

  async function act(id: number, path: string, msg: string, body: Record<string, unknown> = {}) {
    setBusy(id)
    try { await api(path, { method: 'POST', body }); toast(msg); await load() }
    catch (e) { toast(errorMessage(e, 'That did not work')) }
    finally { setBusy(0) }
  }

  if (!lic) return <div className="screen"><div className="spinner" /></div>

  const pending = reqs.filter((r) => r.status === 'pending')
  const active = lic.filter((l) => l.state === 'ok' || l.state === 'expiring')
  const renewals = lic.filter((l) => l.state !== 'revoked' && l.state !== 'expired' && l.days_left != null && l.days_left <= 30)
  const lapsed = lic.filter((l) => l.state === 'expired' || l.state === 'revoked')
  const opened = lic.filter((l) => l.checkins > 0).length
  const perpetual = lic.filter((l) => l.perpetual).length

  const stateCounts: Record<string, number> = {}
  lic.forEach((l) => { stateCounts[l.state] = (stateCounts[l.state] || 0) + 1 })
  const stateSegs = Object.keys(stateCounts).map((s) => ({ label: STATE_LABEL[s] || s, value: stateCounts[s], color: STATE_COLOR[s] || 'var(--ink-faint)' })).sort((a, b) => b.value - a.value)

  const plat: Record<string, number> = { windows: 0, mac: 0, linux: 0 }
  let notOpened = 0
  lic.forEach((l) => { const p = (l.last_platform || '').toLowerCase(); if (p in plat) plat[p]++; else notOpened++ })
  const platRows = [
    { label: 'Windows', value: plat.windows, color: '#0ea5e9' }, { label: 'Mac', value: plat.mac, color: '#111827' },
    { label: 'Linux', value: plat.linux, color: '#f59e0b' }, { label: 'Not opened', value: notOpened, color: 'var(--ink-faint)' },
  ].filter((r) => r.value > 0)
  const termRows = [
    { label: 'Subscription', value: lic.length - perpetual, color: 'var(--brand)' },
    { label: 'Perpetual', value: perpetual, color: '#7b3ff2' },
  ].filter((r) => r.value > 0)

  // Version adoption — which build customers are actually running.
  const verCounts: Record<string, number> = {}
  lic.forEach((l) => { if (l.checkins > 0 && l.last_version) verCounts[l.last_version] = (verCounts[l.last_version] || 0) + 1 })
  const verRows = Object.entries(verCounts).sort((a, b) => b[1] - a[1]).slice(0, 5)
    .map(([v, n], i) => ({ label: `v${v}`, value: n, color: PALETTE[i % PALETTE.length] }))
  const current = rels?.releases.find((r) => r.is_current)?.version || rels?.running || ''
  const onLatest = current ? lic.filter((l) => l.last_version === current).length : 0

  const kpis: { n: number; l: string; t: string; d: Drill }[] = [
    { n: lic.length, l: 'Customers', t: 'var(--brand)', d: 'customers' },
    { n: active.length, l: 'Active licences', t: 'var(--ok)', d: 'active' },
    { n: renewals.length, l: 'Renewals ≤30d', t: 'var(--warn)', d: 'renewals' },
    { n: lapsed.length, l: 'Lapsed', t: 'var(--danger)', d: 'lapsed' },
    { n: pending.length, l: 'Requests waiting', t: 'var(--c-reminders)', d: 'requests' },
    { n: users.length, l: 'User accounts', t: 'var(--ink-soft)', d: 'users' },
  ]
  const quick: { ic: string; l: string; go: () => void }[] = [
    { ic: '🎫', l: 'Issue a licence', go: () => go('licences') },
    { ic: '📣', l: 'Broadcast', go: () => go('licences') },
    { ic: '⬆️', l: 'Release a version', go: () => go('licences') },
    { ic: '👤', l: 'Add a user', go: () => go('admin') },
    { ic: '📥', l: 'Review requests', go: () => setDrill('requests') },
    { ic: '💬', l: ticketsOpen ? `Support (${ticketsOpen})` : 'Support', go: () => go('tickets') },
    { ic: '✉️', l: 'Email / SMTP', go: () => go('mail') },
    { ic: '🗂️', l: 'Masters', go: () => go('masters') },
    { ic: '🕓', l: 'Activity log', go: () => go('activity') },
  ]
  const visitBars = (site?.days || []).slice(-10).map((d, i) => ({ label: d.day.slice(5), value: d.visits, color: PALETTE[i % PALETTE.length] }))

  const licRows = drill === 'active' ? active : drill === 'renewals' ? renewals : drill === 'lapsed' ? lapsed : lic
  const Pill = ({ st }: { st: string }) => <span className="tag" style={{ color: STATE_COLOR[st], borderColor: STATE_COLOR[st] }}>{STATE_LABEL[st] || st}</span>

  return (
    <div className="screen">
      <div className="dash-head">
        <div>
          <h1 className="dash-title">Admin console</h1>
          <p className="dash-sub">Welcome back{user?.name ? `, ${user.name}` : ''} — click any figure to drill in and act.</p>
        </div>
        <button className="btn ghost sm" onClick={() => go('licences')}>Manage licences →</button>
      </div>

      <div className="admin-dash">
        <div className="kpi-grid">
          {kpis.map((k) => (
            <button className={`kpi click${drill === k.d ? ' on' : ''}`} key={k.l} onClick={() => setDrill(k.d)}>
              <span className="kpi-n" style={{ color: k.t }}>{k.n}</span><span className="kpi-l">{k.l}</span>
            </button>
          ))}
        </div>

        <div className="qa-grid">
          {quick.map((q) => (
            <button className="qa" key={q.l} onClick={q.go}>
              <span className="qa-ic">{q.ic}</span><span className="qa-l">{q.l}</span>
            </button>
          ))}
        </div>

        <div className="dash-cards">
          <section className="dash-card"><h3 className="dash-card-h">Licences by state</h3>
            {stateSegs.length ? <Donut segs={stateSegs} total={lic.length} /> : <p className="dash-empty">No licences yet.</p>}</section>
          <section className="dash-card"><h3 className="dash-card-h">By platform</h3>
            {platRows.length ? <Bars rows={platRows} /> : <p className="dash-empty">No check-ins yet.</p>}</section>
          <section className="dash-card"><h3 className="dash-card-h">By plan</h3>
            {termRows.length ? <Bars rows={termRows} /> : <p className="dash-empty">No licences yet.</p>}
            <div className="dash-mini"><span><b>{opened}</b> of {lic.length} copies have been opened</span></div></section>
        </div>

        <div className="dash-cards">
          <section className="dash-card"><h3 className="dash-card-h">Version adoption</h3>
            {verRows.length ? <Bars rows={verRows} /> : <p className="dash-empty">No copies have checked in yet.</p>}</section>
          <section className="dash-card"><h3 className="dash-card-h">Activity · last 30 days</h3>
            {sum ? (<>
              <div className="rep-stat"><span>Total events</span><b>{sum.total}</b></div>
              <div className="rep-stat"><span>Added</span><b>{sum.buckets.added}</b></div>
              <div className="rep-stat"><span>Edited</span><b>{sum.buckets.edited}</b></div>
              <div className="rep-stat"><span>Deleted</span><b>{sum.buckets.deleted}</b></div>
              <div className="rep-stat"><span>Security</span><b>{sum.buckets.security}</b></div>
            </>) : <p className="dash-empty">No activity recorded.</p>}</section>
          <section className="dash-card"><h3 className="dash-card-h">Releases</h3>
            <div className="rep-stat"><span>This installation</span><b>{rels?.running || '—'}</b></div>
            <div className="rep-stat"><span>Current release</span><b>{current || '—'}</b></div>
            <div className="rep-stat"><span>On latest version</span><b>{onLatest} / {lic.length}</b></div>
            <div className="rep-stat"><span>Live customers</span><b>{rels?.customers ?? '—'}</b></div>
            <button className="btn ghost sm" style={{ marginTop: 12 }} onClick={() => go('licences')}>Publish / release →</button>
          </section>
        </div>

        <div className="dash-cards">
          <section className="dash-card"><h3 className="dash-card-h">Website visits</h3>
            <div className="rep-stat"><span>Total opens</span><b>{site?.total ?? '—'}</b></div>
            <div className="rep-stat"><span>Today</span><b>{site?.today ?? '—'}</b></div>
            {visitBars.length ? <div style={{ marginTop: 12 }}><Bars rows={visitBars} /></div>
              : <p className="dash-empty">No visits recorded yet.</p>}</section>
          <section className="dash-card"><h3 className="dash-card-h">Support</h3>
            <div className="rep-stat"><span>Open tickets</span><b style={{ color: ticketsOpen ? 'var(--warn)' : 'var(--ok)' }}>{ticketsOpen ?? '—'}</b></div>
            <p className="dash-empty" style={{ marginTop: 8 }}>Customers raise tickets from the website’s Support form; replies email them back.</p>
            <button className="btn ghost sm" style={{ marginTop: 12 }} onClick={() => go('tickets')}>Open support console →</button></section>
          <section className="dash-card"><h3 className="dash-card-h">Licence requests</h3>
            <div className="rep-stat"><span>Waiting</span><b style={{ color: pending.length ? 'var(--warn)' : 'var(--ok)' }}>{pending.length}</b></div>
            <div className="rep-stat"><span>Total received</span><b>{reqs.length}</b></div>
            <button className="btn ghost sm" style={{ marginTop: 12 }} onClick={() => setDrill('requests')}>Review requests →</button></section>
        </div>

        <section className="dash-card">
          <h3 className="dash-card-h">Recent activity</h3>
          {recent.length === 0 ? <p className="dash-empty">Nothing recorded yet.</p> : (
            <div className="act-list">
              {recent.map((a) => (
                <div className="act-item" key={a.id}>
                  <span className="act-dot" style={{ background: TONE[a.tone] || 'var(--ink-faint)' }} />
                  <div className="act-main">
                    <div className="act-title">{a.verb} {a.entity_label}{a.label ? ` · ${a.label}` : ''}</div>
                    <div className="act-meta">{a.by} · {a.at}{a.security ? ' · security' : ''}</div>
                  </div>
                </div>
              ))}
              <button className="btn ghost sm" style={{ marginTop: 12 }} onClick={() => go('activity')}>Full activity log →</button>
            </div>
          )}
        </section>

        {/* Drill-down: driven by whichever KPI is selected. */}
        <section className="dash-card">
          <div className="drill-head">
            <h3 className="dash-card-h" style={{ margin: 0 }}>{DRILL_TITLE[drill]}
              <span className="lic-badge" style={{ background: 'var(--ink-faint)' }}>{drill === 'requests' ? pending.length : drill === 'users' ? users.length : licRows.length}</span>
            </h3>
          </div>

          {drill === 'requests' ? (
            pending.length === 0 ? <p className="dash-empty">No requests waiting.</p> : (
              <div className="table-wrap"><table className="dtable">
                <thead><tr><th>Name</th><th>Email</th><th>Requested</th><th></th></tr></thead>
                <tbody>{pending.map((r) => (
                  <tr key={r.id}><td>{r.name}</td><td className="muted">{r.email}</td><td className="muted">{fmtDateTime(r.created_at)}</td>
                    <td><div className="row-act">
                      <button className="btn xs" onClick={() => go('licences')}>Approve</button>
                      <button className="btn ghost xs" disabled={busy === r.id} onClick={() => act(r.id, `/api/licence-requests/${r.id}/reject`, 'Request rejected')}>Reject</button>
                    </div></td></tr>
                ))}</tbody>
              </table></div>
            )
          ) : drill === 'users' ? (
            <div className="table-wrap"><table className="dtable">
              <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead>
              <tbody>{users.map((u) => (
                <tr key={u.id} onClick={() => go('admin')}><td>{u.name}</td><td className="muted">{u.email}</td>
                  <td><span className="tag" style={{ color: u.role === 'admin' ? 'var(--brand)' : 'var(--ink-soft)', borderColor: 'var(--line)' }}>{u.role}</span></td>
                  <td className="muted">{u.status}</td>
                  <td><div className="row-act"><button className="btn ghost xs" onClick={(e) => { e.stopPropagation(); go('admin') }}>Manage</button></div></td></tr>
              ))}</tbody>
            </table></div>
          ) : (
            licRows.length === 0 ? <p className="dash-empty">Nothing here.</p> : (
              <div className="table-wrap"><table className="dtable">
                <thead><tr><th>Name</th><th>Email</th><th>State</th><th>Expires</th><th>Last seen</th><th></th></tr></thead>
                <tbody>{licRows.map((l) => (
                  <tr key={l.id}><td>{l.name}</td><td className="muted">{l.email}</td><td><Pill st={l.state} /></td>
                    <td>{l.perpetual ? 'Never' : (l.expires_on || '—')}</td>
                    <td className="muted">{l.checkins > 0 ? l.last_seen : 'never'}</td>
                    <td><div className="row-act">
                      {(l.state === 'expired' || (l.days_left != null && l.days_left <= 30 && l.state !== 'revoked')) && (
                        <button className="btn xs" disabled={busy === l.id} onClick={() => act(l.id, `/api/licences/${l.id}/extend`, 'Extended 1 year', { days: 365 })}>Extend 1y</button>
                      )}
                      {l.state === 'revoked' && (
                        <button className="btn xs" disabled={busy === l.id} onClick={() => act(l.id, `/api/licences/${l.id}/restore`, 'Licence reinstated')}>Restore</button>
                      )}
                      <button className="btn ghost xs" onClick={() => go('licences')}>Manage</button>
                    </div></td></tr>
                ))}</tbody>
              </table></div>
            )
          )}
        </section>
      </div>
    </div>
  )
}
