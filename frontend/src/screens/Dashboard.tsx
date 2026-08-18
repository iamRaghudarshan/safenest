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
import type { DashboardData, BriefingData, BriefingDue, ModuleKey, Habit, Photo } from '../types'
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
      {/* A friendly, original nature scene — warmth on the first screen someone
          sees, and it carries the greeting the plain line used to. */}
      <NatureHero greet={greet} date={brief?.date} />

      {/* Today's habits, front and centre: the tap-to-tick strip is the whole
          point of a habit tracker, so it belongs on the home page, not two taps
          away. Only shown to a copy that has the module and a habit due today. */}
      {can('habits') && <HabitsToday go={go} />}

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

      {/* A gentle auto-playing carousel of recent photos — a warm, personal
          window straight on the home page. */}
      {can('gallery') && <GalleryCarousel go={go} />}

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

      {/* The Modules NAVIGATION grid used to live here. Removed on request: the
          home page is a feed of what the modules contain (dues, safe-to-spend,
          today's habits, the snapshot), not a launcher — the Modules tab is the
          launcher. */}
     </PullToRefresh>
    </div>
  )
}

/** An original, brand-tinted nature scene: sun, clouds, rolling hills and a
 *  growing sprout — the "grow a good habit" motif. Pure inline SVG so it is
 *  asset-free, crisp at any size, and re-colours itself for dark mode via CSS
 *  variables. Deliberately NOT anyone's mascot. */
function NatureHero({ greet, date }: { greet: string; date?: string }) {
  return (
    <div className="nature-hero">
      <svg className="nh-scene" viewBox="0 0 400 210" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <defs>
          <linearGradient id="nhSky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--sky1)" />
            <stop offset="0.6" stopColor="var(--sky2)" />
            <stop offset="1" stopColor="var(--sky3)" />
          </linearGradient>
        </defs>
        <rect width="400" height="210" fill="url(#nhSky)" />

        {/* sun */}
        <circle className="nh-halo" cx="352" cy="40" r="34" fill="var(--sun)" />
        <circle cx="352" cy="40" r="20" fill="var(--sun)" />

        {/* fluffy flat clouds */}
        <g className="nh-cloud1" fill="var(--cloud)">
          <circle cx="70" cy="48" r="16" /><circle cx="92" cy="39" r="22" /><circle cx="117" cy="48" r="16" />
          <rect x="64" y="46" width="60" height="16" rx="8" />
        </g>
        <g className="nh-cloud2" fill="var(--cloud)" opacity="0.94">
          <circle cx="240" cy="34" r="12" /><circle cx="258" cy="26" r="17" /><circle cx="277" cy="34" r="12" />
          <rect x="236" y="32" width="45" height="13" rx="6.5" />
        </g>
        {/* two birds */}
        <g stroke="var(--mtn2)" strokeWidth="2" strokeLinecap="round" fill="none" opacity="0.5">
          <path d="M170 60 q6 -6 12 0 q6 -6 12 0" /><path d="M200 52 q5 -5 10 0 q5 -5 10 0" />
        </g>

        {/* layered snow-capped mountains */}
        <path d="M2 124 L96 50 L190 124 Z" fill="var(--mtn1)" />
        <path d="M96 50 L118 74 Q106 66 96 74 Q86 66 74 74 Z" fill="var(--snow)" />
        <path d="M118 124 L232 40 L346 124 Z" fill="var(--mtn2)" />
        <path d="M232 40 L258 68 Q244 59 232 68 Q220 59 206 68 Z" fill="var(--snow)" />
        <path d="M300 124 L366 66 L400 124 Z" fill="var(--mtn1)" opacity="0.85" />
        <path d="M366 66 L384 86 Q375 79 366 86 Q357 79 348 86 Z" fill="var(--snow)" />

        {/* meadow — three green layers */}
        <path d="M0 126 Q100 114 200 124 T400 120 V210 H0 Z" fill="var(--hill1)" />
        <path d="M0 152 Q120 140 240 150 T400 146 V210 H0 Z" fill="var(--hill2)" />
        <path d="M0 180 Q140 170 280 180 T400 176 V210 H0 Z" fill="var(--hill3)" />

        {/* a lake */}
        <ellipse cx="300" cy="168" rx="48" ry="9" fill="var(--water2)" />
        <ellipse cx="300" cy="166" rx="34" ry="5" fill="var(--water)" opacity="0.8" />

        {/* a pine forest along the hills */}
        {[[26, 132, 0.85], [58, 138, 1.05], [92, 132, 0.75], [340, 134, 1], [372, 130, 0.8], [216, 150, 0.7]].map(([x, y, s], i) => (
          <g key={i} transform={`translate(${x} ${y}) scale(${s})`}>
            <rect x="-2.5" y="0" width="5" height="10" fill="var(--trunk)" />
            <path d="M0 -30 L13 -7 L-13 -7 Z" fill="var(--pine)" />
            <path d="M0 -20 L15 6 L-15 6 Z" fill="var(--pine2)" />
          </g>
        ))}

        {/* a cute bear on the meadow */}
        <g transform="translate(150 188)">
          <ellipse cx="-11" cy="5" rx="7" ry="4.5" fill="var(--bear2)" /><ellipse cx="11" cy="5" rx="7" ry="4.5" fill="var(--bear2)" />
          <ellipse cx="0" cy="-6" rx="16" ry="14" fill="var(--bear)" />
          <circle cx="-11" cy="-23" r="5" fill="var(--bear)" /><circle cx="11" cy="-23" r="5" fill="var(--bear)" />
          <circle cx="-11" cy="-23" r="2.4" fill="var(--bear2)" /><circle cx="11" cy="-23" r="2.4" fill="var(--bear2)" />
          <circle cx="0" cy="-19" r="12" fill="var(--bear)" />
          <ellipse cx="0" cy="-14" rx="6.5" ry="5" fill="var(--bearface)" />
          <circle cx="-4.5" cy="-21" r="1.6" fill="#3a2a1a" /><circle cx="4.5" cy="-21" r="1.6" fill="#3a2a1a" />
          <ellipse cx="0" cy="-16" rx="1.9" ry="1.3" fill="#3a2a1a" />
        </g>

        {/* wildflowers */}
        {[[40, 194], [96, 200], [214, 190], [356, 194]].map(([x, y], i) => (
          <g key={i} transform={`translate(${x} ${y})`}>
            <line x1="0" y1="0" x2="0" y2="10" stroke="var(--stem)" strokeWidth="2" />
            <circle cx="0" cy="-2" r="3.6" fill={i % 2 ? 'var(--petal2)' : 'var(--petal)'} />
            <circle cx="0" cy="-2" r="1.5" fill="#fff" opacity="0.85" />
          </g>
        ))}

        {/* two butterflies */}
        <g className="nh-fly" transform="translate(196 96)">
          <path d="M0 0 Q-11 -9 -13 -1 Q-11 7 0 1 Z" fill="var(--petal)" opacity="0.92" />
          <path d="M0 0 Q11 -9 13 -1 Q11 7 0 1 Z" fill="var(--brand)" opacity="0.8" />
          <line x1="0" y1="-2" x2="0" y2="4" stroke="#3a2f66" strokeWidth="1.3" strokeLinecap="round" />
        </g>
        <g className="nh-fly" transform="translate(268 120)" style={{ animationDelay: '-1.6s' }}>
          <path d="M0 0 Q-9 -7 -11 -1 Q-9 6 0 1 Z" fill="var(--petal2)" opacity="0.92" />
          <path d="M0 0 Q9 -7 11 -1 Q9 6 0 1 Z" fill="var(--water2)" opacity="0.85" />
          <line x1="0" y1="-2" x2="0" y2="3" stroke="#3a2f66" strokeWidth="1.2" strokeLinecap="round" />
        </g>
      </svg>
      <div className="nh-copy">
        <div className="nh-greet">{greet} 👋</div>
        {date && <div className="nh-sub">{date}</div>}
      </div>
    </div>
  )
}

/** The tap-to-tick strip of today's habits, on the home page. */
function HabitsToday({ go }: { go: (k: string) => void }) {
  const [habits, setHabits] = useState<Habit[]>([])
  const reload = useCallback(() => {
    api<{ items: Habit[] }>('/api/habits').then((r) => setHabits(r.items)).catch(() => { })
  }, [])
  useEffect(() => { reload() }, [reload])

  const active = habits.filter((h) => h.active_today)
  if (active.length === 0) return null

  async function tick(h: Habit) {
    await api(`/api/habits/${h.id}/check`, { method: 'POST', body: h.done_today ? { count: 0 } : {} })
    reload()
  }
  const done = active.filter((h) => h.done_today).length

  return (
    <>
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>Today’s habits · {done}/{active.length}</span>
        <button className="linklike" style={{ fontSize: 12.5, fontWeight: 700 }} onClick={() => go('habits')}>All ›</button>
      </div>
      <div className="habits-strip">
        {active.map((h) => {
          const c = h.color || 'var(--c-habits)'
          const isDone = h.done_today
          return (
            <button key={h.id} className="habit-chip" onClick={() => tick(h)}>
              <div className="hc-ring" style={{
                border: `2.5px solid ${c}`,
                background: isDone ? c : `color-mix(in srgb, ${c} 12%, transparent)`,
                color: isDone ? '#fff' : 'inherit',
              }}>{isDone ? '✓' : (h.icon || '◎')}</div>
              <div className="hc-name">{h.name}</div>
              <div className="hc-streak" style={{ color: c }}>{h.current_streak > 0 ? `🔥 ${h.current_streak}` : '·'}</div>
            </button>
          )
        })}
      </div>
    </>
  )
}

/** An auto-playing slideshow of recent photos. Cross-fades every few seconds,
 *  taps through to the Gallery, and quietly renders nothing on a copy with no
 *  photos yet. */
function GalleryCarousel({ go }: { go: (k: string) => void }) {
  const [photos, setPhotos] = useState<Photo[]>([])
  const [idx, setIdx] = useState(0)

  useEffect(() => {
    api<{ items: Photo[] }>('/api/gallery').then((r) => setPhotos(r.items.slice(0, 10))).catch(() => { })
  }, [])
  useEffect(() => {
    if (photos.length < 2) return
    const t = setInterval(() => setIdx((i) => (i + 1) % photos.length), 4200)
    return () => clearInterval(t)
  }, [photos.length])

  if (photos.length === 0) return null
  const cur = photos[idx]
  return (
    <>
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span>Your photos</span>
        <button className="linklike" style={{ fontSize: 12.5, fontWeight: 700 }} onClick={() => go('gallery')}>Open ›</button>
      </div>
      <div className="home-slides" onClick={() => go('gallery')}>
        {photos.map((p, i) => (
          <img key={p.id} src={p.thumb_url || p.url} className={i === idx ? 'on' : ''} alt="" loading="lazy" />
        ))}
        <div className="hs-grad" />
        <span className="hs-badge">📸 Gallery</span>
        <div className="hs-dots">
          {photos.map((p, i) => <span key={p.id} className={`hs-dot${i === idx ? ' on' : ''}`} />)}
        </div>
        {(cur.caption || cur.taken_fmt) && (
          <div className="hs-cap">
            {cur.caption && <div className="t">{cur.caption}</div>}
            {cur.taken_fmt && <div className="s">{cur.taken_fmt}</div>}
          </div>
        )}
      </div>
    </>
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
