// Activity log — the MySQL audit trail rendered as something readable.
//
// Every add, edit and delete is already recorded server-side; this screen shows
// what happened, to what, and for edits exactly which fields changed. You see your
// own activity; an administrator sees everyone's and can filter by person.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { useNav } from '../nav'
import { TopBar, Spinner, Empty } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { fmtDate, fmtDateTime, fmtTime, istDayISO, todayISO } from '../format'
import type { ActivityRow, ActivitySummary } from '../types'
import { appName } from '../branding'

type Kind = 'all' | 'data' | 'security'
const PAGE = 50

// Colour carries severity; the glyph carries meaning. Without the per-action
// overrides a sign-in and an edit both show a pencil, which reads as a change to
// your data when it isn't one.
const TONE_ICON: Record<string, string> = { ok: '＋', info: '✎', warn: '!', danger: '✕' }
const ACTION_ICON: Record<string, string> = {
  login: '→', login_failed: '✕', login_locked: '⊘', login_suspended: '⊘',
  password_change: '⚿', password_change_failed: '⚿',
  reveal: '◉', export_bundle: '⤓', purge_cdn: '↻',
  permission_change: '⚑', user_create: '⚑', user_update: '⚑', user_delete: '⚑',
  upload: '↑', scan: '⧉', restore: '↺', trash: '🗑', empty_trash: '🗑',
  done: '✓', reopen: '↺', loan_paid: '✓', card_paid: '✓',
  push_subscribe: '◉', tag_person: '◉',
}
const iconFor = (r: { action: string; tone: string }) =>
  ACTION_ICON[r.action] || TONE_ICON[r.tone] || '•'

/** "Today", "Yesterday", or a plain date — a log is scanned by day. */
function dayLabel(iso: string | null): string {
  if (!iso) return 'Unknown'
  const d = iso.slice(0, 10)
  if (d === todayISO()) return 'Today'
  if (d === istDayISO(-1)) return 'Yesterday'
  return fmtDate(d)
}

const timeOf = (iso: string | null) => fmtTime(iso)

/** A "History" link from a record hands us its identity as JSON in the nav intent. */
interface Focus { entity: string; id: number; label: string | null }

function readFocus(raw: string | null): Focus | null {
  if (!raw) return null
  try {
    const f = JSON.parse(raw)
    return f && typeof f.entity === 'string' && typeof f.id === 'number'
      ? { entity: f.entity, id: f.id, label: f.label ?? null } : null
  } catch { return null }
}

export default function Activity() {
  const { back, canBack, takeIntent } = useNav()
  const { user } = useAuth()
  const [focus, setFocus] = useState<Focus | null>(null)
  const [kind, setKind] = useState<Kind>('all')
  const [module, setModule] = useState('')   // '' = every part of the app
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<ActivityRow[]>([])
  const [total, setTotal] = useState(0)
  const [sum, setSum] = useState<ActivitySummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState(false)
  const offsetRef = useRef(0)
  const busyRef = useRef(false)

  // Consumed once, on arrival — going "back" and returning shows the full log.
  useEffect(() => { setFocus(readFocus(takeIntent())) }, [takeIntent])

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(typed.trim()), 300)
    return () => window.clearTimeout(t)
  }, [typed])

  const load = useCallback(async (reset: boolean, silent = false) => {
    if (busyRef.current) return
    busyRef.current = true
    const off = reset ? 0 : offsetRef.current
    if (reset && !silent) setLoading(true)
    if (!reset) setMore(true)
    try {
      const p = new URLSearchParams({ offset: String(off), limit: String(PAGE), kind })
      if (query) p.set('q', query)
      if (module) p.set('module', module)
      if (focus) { p.set('entity', focus.entity); p.set('entity_id', String(focus.id)) }
      const d = await api<{ items: ActivityRow[]; total: number }>(`/api/activity?${p}`)
      setTotal(d.total)
      setRows((prev) => (reset ? d.items : [...prev, ...d.items]))
      offsetRef.current = off + d.items.length
    } catch { /* the connection banner already says what's wrong */ }
    finally { setLoading(false); setMore(false); busyRef.current = false }
  }, [kind, query, focus, module])

  useEffect(() => { offsetRef.current = 0; load(true) }, [load])

  const loadSummary = useCallback(() => {
    api<ActivitySummary>('/api/activity/summary?days=30').then(setSum).catch(() => setSum(null))
  }, [])
  useEffect(() => { loadSummary() }, [loadSummary])

  const refresh = useCallback(async () => {
    offsetRef.current = 0
    loadSummary()
    await load(true, true)
  }, [load, loadSummary])

  // Group consecutive rows under a day heading.
  const groups: { day: string; items: ActivityRow[] }[] = []
  for (const r of rows) {
    const day = dayLabel(r.at)
    if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, items: [r] })
    else groups[groups.length - 1].items.push(r)
  }

  return (
    <div className="screen">
      <TopBar title="Activity" onBack={canBack ? back : undefined}
        sub={total ? `${total.toLocaleString()} record${total === 1 ? '' : 's'}` : undefined} />

      {/* Focused on one record: the counters and tabs describe the whole log, so
          they'd be misleading here. A chip says what's being shown and undoes it. */}
      {focus ? (
        <div className="act-focus">
          <span className="act-focus-ic">⟲</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="act-focus-t">History of this {focus.entity === 'task' ? 'task' : focus.entity}</div>
            {focus.label && <div className="act-focus-s">{focus.label}</div>}
          </div>
          <button className="btn ghost sm" onClick={() => setFocus(null)}>Show all</button>
        </div>
      ) : (
        <>
          {sum && (
            <div className="act-stats">
              <div className="act-stat"><b>{sum.buckets.added.toLocaleString()}</b><span>Added</span></div>
              <div className="act-stat"><b>{sum.buckets.edited.toLocaleString()}</b><span>Edited</span></div>
              <div className="act-stat"><b>{sum.buckets.deleted.toLocaleString()}</b><span>Deleted</span></div>
              <div className="act-stat"><b>{sum.buckets.security.toLocaleString()}</b><span>Account</span></div>
            </div>
          )}

          <div className="seg4">
            {(['all', 'data', 'security'] as Kind[]).map((k) => (
              <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
                {k === 'all' ? 'Everything' : k === 'data' ? 'My records' : 'Account'}
              </button>
            ))}
          </div>

          {/* Narrow to one part of the app. Only modules that have actually been
              used are listed, so the row doesn't fill with empty options. */}
          {!!sum?.modules.length && (
            <div className="act-mods">
              <button className={`chip${module ? '' : ' on'}`} onClick={() => setModule('')}>
                All
              </button>
              {sum.modules.map((m) => (
                <button key={m.key} className={`chip${module === m.key ? ' on' : ''}`}
                  onClick={() => setModule(module === m.key ? '' : m.key)}>
                  {m.label} <span className="act-mod-n">{m.count.toLocaleString()}</span>
                </button>
              ))}
            </div>
          )}

          <div className="searchbar">
            <span className="searchbar-ic" aria-hidden="true">🔍</span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} type="search"
              autoComplete="off" placeholder="Search the log" aria-label="Search activity" />
            {!!typed && <button className="searchbar-x" onClick={() => setTyped('')} aria-label="Clear search">✕</button>}
          </div>
        </>
      )}

      <PullToRefresh onRefresh={refresh}>
        {loading ? <Spinner />
          : rows.length === 0
            ? <Empty icon="📋"
                title={focus ? 'No history yet' : query ? 'No matches' : 'Nothing recorded yet'}
                hint={focus
                  ? 'This record has not been changed since it was added.'
                  : query ? undefined
                    : `Adds, edits and deletions will appear here as you use ${appName()}.`} />
            : (
              <>
                {groups.map((g) => (
                  <div key={g.day}>
                    <div className="section-title">{g.day}</div>
                    <div className="list">
                      {g.items.map((r) => <Row key={r.id} r={r} showWho={user?.role === 'admin'} />)}
                    </div>
                  </div>
                ))}
                {rows.length < total && (
                  <button className="btn ghost block" style={{ marginTop: 14 }}
                    disabled={more} onClick={() => load(false)}>
                    {more ? 'Loading…' : `Show older (${(total - rows.length).toLocaleString()} more)`}
                  </button>
                )}
                {rows.length >= total && sum && (
                  <p className="muted act-foot">
                    Recording since {fmtDateTime(sum.tracking_since)?.split(',')[0]}. Nothing here
                    can be edited or removed from inside the app.
                  </p>
                )}
              </>
            )}
      </PullToRefresh>
    </div>
  )
}

function Row({ r, showWho }: { r: ActivityRow; showWho: boolean }) {
  const [open, setOpen] = useState(false)
  const hasDetail = r.changes.length > 0

  return (
    <div className={`card act-row${hasDetail ? ' tappable' : ''}`}
      onClick={hasDetail ? () => setOpen((o) => !o) : undefined}>
      <div className="act-top">
        <span className={`act-dot ${r.tone}`}>{iconFor(r)}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="act-title">{r.title}</div>
          <div className="act-sub">
            {timeOf(r.at)}
            {/* Who did it, always — an admin reading a mixed feed needs it on every
                row, including their own, or they have to infer it from absence. */}
            {showWho && ` · ${r.by}`}
            {r.ip && ` · ${r.ip}`}
            {hasDetail && ` · ${r.changes.length} change${r.changes.length === 1 ? '' : 's'}`}
          </div>
        </div>
        {hasDetail && <span className="act-caret">{open ? '⌃' : '⌄'}</span>}
      </div>

      {open && hasDetail && (
        <div className="act-changes">
          {r.changes.map((c) => (
            <div key={c.field} className="act-change">
              <span className="act-field">{c.field}</span>
              <span className="act-from">{c.from ?? '—'}</span>
              <span className="act-arrow">→</span>
              <span className="act-to">{c.to ?? '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
