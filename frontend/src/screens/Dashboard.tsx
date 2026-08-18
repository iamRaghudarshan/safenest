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
      <svg className="nh-scene" viewBox="0 0 400 200" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <defs>
          <linearGradient id="nhSky" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--sky1)" />
            <stop offset="0.55" stopColor="var(--sky2)" />
            <stop offset="1" stopColor="var(--sky3)" />
          </linearGradient>
        </defs>
        <rect width="400" height="200" fill="url(#nhSky)" />
        {/* sun with a breathing halo and soft rays */}
        <circle className="nh-halo" cx="352" cy="36" r="36" fill="var(--sun)" />
        <g stroke="var(--sun)" strokeWidth="3" strokeLinecap="round" opacity="0.5">
          <line x1="352" y1="-6" x2="352" y2="4" /><line x1="384" y1="6" x2="377" y2="13" /><line x1="320" y1="6" x2="327" y2="13" />
        </g>
        <circle cx="352" cy="36" r="19" fill="var(--sun)" />
        {/* a little flock of birds */}
        <g stroke="#5b6b86" strokeWidth="2" strokeLinecap="round" fill="none" opacity="0.5">
          <path d="M104 30 q6 -6 12 0 q6 -6 12 0" /><path d="M132 24 q5 -5 10 0 q5 -5 10 0" /><path d="M156 32 q4 -4 8 0 q4 -4 8 0" />
        </g>
        {/* drifting clouds */}
        <g className="nh-cloud1" fill="var(--cloud)" opacity="0.96">
          <ellipse cx="74" cy="30" rx="27" ry="12" /><ellipse cx="98" cy="25" rx="18" ry="11" /><ellipse cx="50" cy="26" rx="15" ry="9" />
        </g>
        <g className="nh-cloud2" fill="var(--cloud)" opacity="0.8">
          <ellipse cx="212" cy="22" rx="21" ry="9" /><ellipse cx="230" cy="18" rx="13" ry="8" />
        </g>
        {/* snow-capped mountains, far back */}
        <g>
          <path d="M14 116 L92 48 L170 116 Z" fill="var(--mtn1)" />
          <path d="M92 48 L114 68 Q103 61 92 68 Q81 61 70 68 Z" fill="var(--snow)" />
          <path d="M140 116 L226 40 L318 116 Z" fill="var(--mtn2)" />
          <path d="M226 40 L250 63 Q237 55 226 63 Q214 55 202 63 Z" fill="var(--snow)" />
        </g>
        {/* three layered hills */}
        <path d="M0 118 Q80 90 190 114 T400 106 V200 H0 Z" fill="var(--hill1)" />
        <path d="M0 138 Q120 112 250 132 T400 126 V200 H0 Z" fill="var(--hill2)" />
        <path d="M0 160 Q150 140 280 158 T400 152 V200 H0 Z" fill="var(--hill3)" />

        {/* a waterfall tumbling off a rocky cliff on the right, into a pond */}
        <g>
          <path d="M322 158 L322 76 Q344 70 366 80 L366 158 Z" fill="var(--rock)" />
          <path d="M322 76 Q344 70 366 80 L366 90 Q344 84 322 90 Z" fill="var(--rock2)" />
          <rect x="336" y="82" width="16" height="76" rx="6" fill="var(--water)" opacity="0.92" />
          <g className="nh-fall" fill="var(--foam)" opacity="0.85">
            <rect x="339" y="82" width="2.4" height="22" rx="1.2" />
            <rect x="345" y="100" width="2.4" height="24" rx="1.2" />
            <rect x="342" y="124" width="2.4" height="26" rx="1.2" />
          </g>
          <ellipse cx="344" cy="162" rx="31" ry="7" fill="var(--water2)" />
          <ellipse cx="344" cy="156" rx="9" ry="3" fill="var(--foam)" opacity="0.9" />
          <ellipse className="nh-ripple" cx="344" cy="163" rx="20" ry="4" fill="none" stroke="var(--foam)" strokeWidth="1.4" opacity="0.7" />
        </g>

        {/* a small pine forest */}
        {[[214, 150, 0.8], [250, 156, 1], [278, 162, 0.85]].map(([x, y, s], i) => (
          <g key={i} transform={`translate(${x} ${y}) scale(${s})`}>
            <rect x="-2" y="0" width="4" height="10" fill="var(--trunk)" />
            <path d="M0 -28 L11 -8 L-11 -8 Z" fill="var(--pine)" />
            <path d="M0 -20 L13 4 L-13 4 Z" fill="var(--pine2)" />
          </g>
        ))}

        {/* a deer grazing on the middle hill */}
        <g transform="translate(150 150)" fill="var(--deer)">
          <ellipse cx="0" cy="-9" rx="10" ry="5.5" />
          <rect x="-8" y="-6" width="2.3" height="9" rx="1" /><rect x="-3" y="-6" width="2.3" height="9" rx="1" />
          <rect x="4" y="-6" width="2.3" height="9" rx="1" /><rect x="8" y="-6" width="2.3" height="9" rx="1" />
          <path d="M9 -12 Q15 -13 15 -21 L18 -21 Q19 -12 12 -8 Z" />
          <circle cx="17" cy="-22" r="3.1" />
          <path d="M15 -25 l-2 -6 M19 -25 l2 -6" stroke="var(--deer)" strokeWidth="1.6" strokeLinecap="round" />
        </g>

        {/* a rabbit hopping in the foreground */}
        <g className="nh-hop" transform="translate(96 184)" fill="var(--animal2)">
          <ellipse cx="0" cy="-5" rx="6.5" ry="5.5" />
          <circle cx="6" cy="-9" r="3.6" />
          <ellipse cx="5" cy="-15" rx="1.5" ry="4.2" /><ellipse cx="8.6" cy="-15" rx="1.5" ry="4.2" />
          <circle cx="-6" cy="-3" r="2.2" fill="var(--foam)" />
          <circle cx="7.6" cy="-9.4" r="0.8" fill="#2a2a2a" />
        </g>

        {/* wildflowers */}
        {[[54, 168], [176, 176], [300, 180]].map(([x, y], i) => (
          <g key={i} transform={`translate(${x} ${y})`}>
            <line x1="0" y1="0" x2="0" y2="10" stroke="var(--stem)" strokeWidth="2" />
            <circle cx="0" cy="-2" r="3.6" fill={i % 2 ? 'var(--petal2)' : 'var(--petal)'} />
            <circle cx="0" cy="-2" r="1.5" fill="#fff" opacity="0.85" />
          </g>
        ))}

        {/* the growing sprout — gently swaying */}
        <g className="nh-sprout" transform="translate(40 158)">
          <path d="M0 22 L0 -4" stroke="var(--stem)" strokeWidth="3" strokeLinecap="round" fill="none" />
          <path d="M0 4 Q-16 0 -21 -14 Q-5 -14 0 3 Z" fill="var(--leaf)" />
          <path d="M0 -2 Q16 -7 21 -21 Q5 -19 0 -4 Z" fill="var(--leaf)" />
        </g>

        {/* butterfly, floating */}
        <g className="nh-fly" transform="translate(150 70)">
          <path d="M0 0 Q-12 -10 -14 -1 Q-12 8 0 1 Z" fill="var(--petal)" opacity="0.9" />
          <path d="M0 0 Q12 -10 14 -1 Q12 8 0 1 Z" fill="var(--brand)" opacity="0.8" />
          <line x1="0" y1="-2" x2="0" y2="4" stroke="#4a3f7a" strokeWidth="1.4" strokeLinecap="round" />
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
