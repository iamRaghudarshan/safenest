import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'
import { useNav } from '../nav'
import { NotificationBell } from '../NotificationBell'
import { useToast } from '../toast'
import { useAttention } from '../attention'
import { money, dueLabel } from '../format'
import { Spinner } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { IcWallet, IcBell, IcImage } from '../icons'
import type { DashboardData, BriefingData, BriefingDue, ModuleKey } from '../types'
import { MODULES } from '../modules'

export default function Dashboard() {
  const { user, can } = useAuth()
  const { go } = useNav()
  const toast = useToast()
  const { refresh: refreshAttn } = useAttention()
  const [data, setData] = useState<DashboardData | null>(null)
  const [brief, setBrief] = useState<BriefingData | null>(null)

  const load = useCallback(async () => {
    try { setData(await api<DashboardData>('/api/dashboard')) } catch { /* keep */ }
    try { setBrief(await api<BriefingData>('/api/briefing')) } catch { /* keep */ }
  }, [])
  useEffect(() => { load() }, [load])

  async function payDue(d: BriefingDue) {
    await api(`/api/${d.payType === 'card' ? 'cards' : 'loans'}/${d.id}/pay`, { method: 'POST', body: {} })
    await load(); refreshAttn(); toast('Marked paid ✓')
  }

  const now = new Date()
  const hour = now.getHours()
  const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  const first = user?.name?.split(' ')[0] || ''

  if (!data) return <div className="screen"><Spinner /></div>

  const dues = brief?.dues || []
  const dueGroups: { label: string; items: BriefingDue[] }[] = [
    { label: 'Overdue', items: dues.filter((d) => d.days < 0) },
    { label: 'Today', items: dues.filter((d) => d.days === 0) },
    { label: 'This week', items: dues.filter((d) => d.days >= 1 && d.days <= 7) },
    { label: 'Later this month', items: dues.filter((d) => d.days > 7) },
  ].filter((g) => g.items.length > 0)

  const quickActions = [
    can('expenses') && { key: 'exp', label: 'Add expense', Icon: IcWallet, color: 'var(--c-expenses)', on: () => go('expenses', 'add') },
    can('reminders') && { key: 'rem', label: 'Add reminder', Icon: IcBell, color: 'var(--c-reminders)', on: () => go('reminders', 'add') },
    can('gallery') && { key: 'gal', label: 'Photos', Icon: IcImage, color: 'var(--c-gallery)', on: () => go('gallery') },
  ].filter(Boolean) as { key: string; label: string; Icon: (p: { className?: string }) => React.ReactElement; color: string; on: () => void }[]

  return (
    <div className="screen">
     <PullToRefresh onRefresh={load}>
      <div className="topbar home-bar">
        <div className="topbar-text">
          <h1>Hi {first}</h1>
        </div>
        <div className="topbar-actions">
          <button className="icon-btn" onClick={() => go('search')} aria-label="Search everything">
            <span style={{ fontSize: 19 }}>🔎</span>
          </button>
          <NotificationBell />
          <button className="avatar-btn" onClick={() => go('profile')} aria-label="Your profile">
            {user?.avatar_url
              ? <img src={user.avatar_url} className="avatar-img" alt="" />
              : <span className="avatar">{user?.initials}</span>}
          </button>
        </div>
      </div>
      {/* Below the bar, not inside it: the date is a full weekday and month name
          ("Wednesday, 29 July"), which never fit in the ~230px left beside the
          buttons. On its own line it has the whole width and always shows. */}
      <div className="home-greeting">{greet} 👋{brief ? ` · ${brief.date}` : ''}</div>

      {/* Daily Briefing: Safe to spend */}
      {brief && brief.safeToSpend.hasIncome && <SafeToSpend s={brief.safeToSpend} />}
      {brief && !brief.streak.todayLogged && (
        <button className="card nudge" onClick={() => go('expenses', 'add')}>
          <span style={{ fontSize: 18 }}>✍️</span>
          <span style={{ fontWeight: 700, fontSize: 13.5 }}>Log today’s spending{brief.streak.current > 0 ? ` to keep your ${brief.streak.current}-day streak` : ''}</span>
          <span className="spacer" /><span className="muted" style={{ fontSize: 18 }}>›</span>
        </button>
      )}

      {/* Unified DUE feed across every module (with an all-clear empty state) */}
      {brief && (dues.length > 0 ? (
        <>
          <div className="section-title">Due · {dues.length} item{dues.length === 1 ? '' : 's'}</div>
          {dueGroups.map((g) => (
            <div key={g.label}>
              <div className="due-head">{g.label}</div>
              <div className="list" style={{ gap: 8 }}>
                {g.items.map((d) => <DueRow key={d.module + d.kind + d.id} d={d} onPay={payDue} onOpen={() => go(d.module)} />)}
              </div>
            </div>
          ))}
        </>
      ) : (
        <div className="allclear">
          <div className="ac-icon">✅</div>
          <div>
            <div className="ac-title">All clear!</div>
            <div className="ac-sub">Nothing due right now — bills paid, tasks done. Enjoy your day 🎉</div>
          </div>
        </div>
      ))}

      {/* Quick actions */}
      {quickActions.length > 0 && (
        <div className="quick-row" style={{ marginTop: 14 }}>
          {quickActions.map((a) => (
            <button key={a.key} className="quick-act" onClick={a.on}>
              <div className="qa-icon" style={{ background: a.color }}><a.Icon className="ic" /></div>
              <span>{a.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* On this day memory */}
      {brief && brief.memory && (
        <button className="card memory-card" onClick={() => go('gallery')}>
          <img src={brief.memory.thumb_url} alt="" />
          <div>
            <div style={{ fontWeight: 800, fontSize: 15 }}>📸 On this day</div>
            <div className="sub" style={{ fontSize: 12.5 }}>{brief.memory.label}</div>
          </div>
          <span className="spacer" /><span className="muted" style={{ fontSize: 18 }}>›</span>
        </button>
      )}

      {/* Snapshot: portfolio / owe / spent */}
      <div className="section-title">Snapshot</div>
      <div className="card" style={{ display: 'flex', alignItems: 'center', padding: 16 }}>
        <button style={{ flex: 1, textAlign: 'left' }} onClick={() => go('investments')}>
          <div className="mcard-label">Portfolio</div>
          <div className="tabnum" style={{ fontSize: 18, fontWeight: 800 }}>{money(data.stats.investValue, true)}</div>
        </button>
        <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)' }} />
        <button style={{ flex: 1, textAlign: 'left', paddingLeft: 14 }} onClick={() => go('loans')}>
          <div className="mcard-label">You owe</div>
          <div className="tabnum" style={{ fontSize: 18, fontWeight: 800 }}>{money(data.stats.outstanding, true)}</div>
        </button>
        <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)' }} />
        <button style={{ flex: 1, textAlign: 'left', paddingLeft: 14 }} onClick={() => go('expenses')}>
          <div className="mcard-label">Spent</div>
          <div className="tabnum" style={{ fontSize: 18, fontWeight: 800, color: 'var(--danger)' }}>{money(data.stats.monthSpend, true)}</div>
        </button>
      </div>

      {/* Modules grid */}
      <div className="section-title">Modules</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {(Object.keys(MODULES) as ModuleKey[]).filter((k) => can(k)).map((k) => {
          const m = MODULES[k]
          const total = data.moduleTotals[k]
          const Icon = m.Icon
          return (
            <button key={k} className="card" style={{ textAlign: 'left', display: 'flex', alignItems: 'center', gap: 12 }} onClick={() => go(k)}>
              <div style={{ position: 'relative', width: 42, height: 42, borderRadius: 13, display: 'grid', placeItems: 'center', color: '#fff', background: m.color }}>
                <Icon className="ic" />
                {(data.moduleAttention?.[k] ?? 0) > 0 && <span className={`attn-badge${data.moduleAttention![k] >= 3 ? ' pulse' : ''}`}>{data.moduleAttention![k] > 9 ? '9+' : data.moduleAttention![k]}</span>}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 14 }}>{m.label}</div>
                <div className="sub" style={{ fontSize: 12 }}>{m.metric ? m.metric(total) : `${total ?? 0} items`}</div>
              </div>
            </button>
          )
        })}
      </div>
     </PullToRefresh>
    </div>
  )
}

function SafeToSpend({ s }: { s: BriefingData['safeToSpend'] }) {
  const over = s.monthOver > 0
  return (
    <div className="hero-card" style={over ? { background: 'linear-gradient(135deg,#e5484d,#f97316)', boxShadow: '0 16px 40px rgba(229,72,77,.4)' } : undefined}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ position: 'relative', flexShrink: 0 }}>
          <Ring pct={s.ringPct} />
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', textAlign: 'center' }}>
            <div>
              <div style={{ fontSize: 9, opacity: 0.85, textTransform: 'uppercase', letterSpacing: '0.05em' }}>today</div>
              <div className="tabnum" style={{ fontSize: 13, fontWeight: 800, lineHeight: 1.1 }}>{money(s.spentToday, true)}</div>
            </div>
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, opacity: 0.9, fontWeight: 600 }}>{over ? 'Over budget this month' : 'Safe to spend today'}</div>
          <div className="tabnum" style={{ fontSize: 30, fontWeight: 800, letterSpacing: '-0.02em', margin: '1px 0' }}>
            {over ? money(s.monthOver) : money(Math.max(0, s.remainingToday))}
          </div>
          <div style={{ fontSize: 12, opacity: 0.85 }}>
            {over
              ? `${money(s.spentMonth, true)} spent · ${money(s.monthlyBudget, true)} budget`
              : `of ${money(s.allowanceToday, true)} today · ${money(s.monthlyBudget, true)}/mo`}
          </div>
        </div>
      </div>
    </div>
  )
}

function Ring({ pct }: { pct: number }) {
  const size = 78, sw = 8, r = (size - sw) / 2, c = 2 * Math.PI * r
  const p = Math.min(1, Math.max(0, pct))
  return (
    <svg width={size} height={size} style={{ display: 'block' }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,.22)" strokeWidth={sw} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#fff" strokeWidth={sw} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - p)}
        transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dashoffset .5s' }} />
    </svg>
  )
}

function DueRow({ d, onPay, onOpen }: { d: BriefingDue; onPay: (d: BriefingDue) => void; onOpen: () => void }) {
  const dl = dueLabel(d.days)
  const color = MODULES[d.module as ModuleKey]?.color || 'var(--brand)'
  const source = MODULES[d.module as ModuleKey]?.label || 'Reminder'
  const toneVar = dl.tone === 'muted' ? 'var(--ink-soft)' : `var(--${dl.tone})`
  return (
    <div className="card duerow">
      <span className="due-dot" style={{ background: color }} />
      <button className="due-main" onClick={onOpen}>
        <div className="due-title">{d.title}</div>
        <div className="due-sub">
          {d.amount ? `${money(d.amount)} · ` : ''}{source}{d.kind === 'renewal' ? ' renewal' : ''}
          {d.payable
            ? <> · <span style={{ color: toneVar, fontWeight: 700 }}>{dl.text}</span></>
            : ` · ${d.due_fmt}`}
        </div>
      </button>
      {d.payable
        ? <button className="btn sm due-pay" onClick={() => onPay(d)}>Pay</button>
        : <span className={`pill ${dl.tone}`}>{dl.text}</span>}
    </div>
  )
}
