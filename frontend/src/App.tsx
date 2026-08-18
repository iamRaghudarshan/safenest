import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useAuth } from './auth'
import { useNav } from './nav'
import { useAttention } from './attention'
import { ConnectionBanner } from './Connection'
import { api } from './api'
import { getSettings, syncSubscription } from './notifications'
import Login from './screens/Login'
import Activation from './screens/Activation'
import Dashboard from './screens/Dashboard'
import Modules from './screens/Modules'
import Loans from './screens/Loans'
import Cards from './screens/Cards'
import Insurance from './screens/Insurance'
import Investments from './screens/Investments'
import Expenses from './screens/Expenses'
import Reminders from './screens/Reminders'
import Todos from './screens/Todos'
import Habits from './screens/Habits'
import Vault from './screens/Vault'
import Gallery from './screens/Gallery'
import { UploadBar } from './screens/UploadBar'
import Documents from './screens/Documents'
import Masters from './screens/Masters'
import Profile from './screens/Profile'
import Activity from './screens/Activity'
import Notifications from './screens/Notifications'
import Admin from './screens/Admin'
import AdminDashboard from './screens/AdminDashboard'
import MailSettings from './screens/MailSettings'
import Tickets from './screens/Tickets'
import Licences from './screens/Licences'
import Search from './screens/Search'
import { IcHome, IcModules, IcBell, IcUser, IcWallet, IcImage, IcShield, IcLock, IcDoc, IcTrend, IcCheck, IcLogout } from './icons'
import { MODULES } from './modules'
import { useBranding } from './branding'
import type { ModuleKey } from './types'

const SCREENS: Record<string, () => React.ReactElement> = {
  home: Dashboard, modules: Modules, reminders: Reminders, profile: Profile,
  loans: Loans, cards: Cards, insurance: Insurance, investments: Investments,
  expenses: Expenses, todo: Todos, habits: Habits, vault: Vault, gallery: Gallery, documents: Documents,
  masters: Masters, admin: Admin, activity: Activity, notifications: Notifications,
  licences: Licences, search: Search, analytics: AdminDashboard, mail: MailSettings, tickets: Tickets,
}

// Bottom nav. `mod` (when set) gates the tab behind that module's permission.
const TABS: { key: string; label: string; Icon: (p: { className?: string }) => React.ReactElement; mod?: ModuleKey }[] = [
  { key: 'home', label: 'Home', Icon: IcHome },
  { key: 'modules', label: 'Modules', Icon: IcModules },
  { key: 'expenses', label: 'Expenses', Icon: IcWallet, mod: 'expenses' },
  { key: 'reminders', label: 'Reminders', Icon: IcBell, mod: 'reminders' },
  { key: 'gallery', label: 'Gallery', Icon: IcImage, mod: 'gallery' },
  { key: 'profile', label: 'Profile', Icon: IcUser },
]

// Sidebar-only search glyph (icons.tsx has no magnifier).
const IcSearch = (p: { className?: string }) => (
  <svg className={p.className} width="20" height="20" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
  </svg>
)
const IcMail = (p: { className?: string }) => (
  <svg className={p.className} width="20" height="20" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" />
  </svg>
)
const IcTicket = (p: { className?: string }) => (
  <svg className={p.className} width="20" height="20" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
)

// Order for the desktop sidebar's module list (most-used first).
const MODULE_ORDER: ModuleKey[] = [
  'expenses', 'loans', 'cards', 'insurance', 'investments',
  'reminders', 'todo', 'habits', 'gallery', 'vault', 'documents',
]

/** The desktop web-app sidebar. Hidden on phones by CSS (the bottom tab bar wins
 *  there); shown at >=1024px. Reuses the same nav store and permission checks as
 *  the tab bar, so it exposes EVERY section — including the admin ones that the
 *  phone can only reach by drilling through Modules/Profile. */
function Sidebar() {
  const { user, can, logout } = useAuth()
  const { route, go } = useNav()
  const brand = useBranding()
  const initials = (user?.name || user?.email || '?').trim().charAt(0).toUpperCase()

  const Item = (key: string, label: string,
                Icon: (p: { className?: string }) => React.ReactElement, color?: string) => (
    <button key={key} className={`snav ${route === key ? 'on' : ''}`} onClick={() => go(key)}>
      <span className="snav-ic" style={color ? { color } : undefined}><Icon /></span>{label}
    </button>
  )

  return (
    <aside className="sidebar">
      <div className="sb-brand">
        {brand.icon_version > 0
          ? <img src={brand.icons['192']} alt="" />
          : <span style={{ fontSize: 22 }}>₹</span>}
        <span>{brand.app_name}</span>
      </div>
      <nav className="sb-nav">
        <div className="sb-group">
          {Item('home', 'Home', IcHome)}
          {Item('search', 'Search', IcSearch)}
        </div>
        {user?.can_admin && (
          <>
            <div className="sb-label">Administration</div>
            <div className="sb-group">
              {Item('analytics', 'Analytics', IcTrend)}
              {Item('admin', 'Users & Admin', IcShield)}
              {Item('licences', 'Licences', IcLock)}
              {Item('tickets', 'Support', IcTicket)}
              {Item('masters', 'Masters', IcDoc)}
              {Item('mail', 'Email / SMTP', IcMail)}
              {Item('activity', 'Activity', IcCheck)}
            </div>
          </>
        )}
        <div className="sb-label">Modules</div>
        <div className="sb-group">
          {MODULE_ORDER.filter((k) => can(k)).map((k) =>
            Item(k, MODULES[k].label, MODULES[k].Icon, MODULES[k].color))}
        </div>
      </nav>
      <div className="sb-foot">
        {Item('notifications', 'Notifications', IcBell)}
        {Item('profile', 'Profile', IcUser)}
        <button className="sb-user" onClick={logout} title="Sign out">
          <span className="avatar-sm">{initials}</span>
          <span className="sb-user-name">{user?.name || user?.email}</span>
          <span className="sb-out"><IcLogout /></span>
        </button>
      </div>
    </aside>
  )
}

export default function App() {
  const { user, ready, can, licenceBlock } = useAuth()
  const { route, go, back, canBack, hasOverlay } = useNav()
  const { total: attnTotal } = useAttention()
  // A newer version on offer (customer copies only). Shown as a banner the moment
  // the app opens online, so nobody has to visit Profile to discover an update.
  const [upd, setUpd] = useState<{ version: string } | null>(null)

  // The bar stays on every screen, including drill-downs like Loans or Vault, so
  // switching sections is always one tap away. (iOS often hides it on a pushed
  // detail view; here the modules are destinations in their own right, not leaf
  // details, so keeping it is the more useful behaviour.)

  // iOS-style left-edge swipe-back. iPhones have no back button, so a swipe from
  // the screen's left edge dismisses the top sheet/lightbox if one is open, else
  // pops the nav stack.
  const swipe = useRef({ back, canBack, hasOverlay })
  swipe.current = { back, canBack, hasOverlay }
  useEffect(() => {
    let sx = 0, sy = 0, tracking = false
    const onStart = (e: TouchEvent) => {
      const t = e.touches[0]
      tracking = t.clientX <= 30
      sx = t.clientX; sy = t.clientY
    }
    const onEnd = (e: TouchEvent) => {
      if (!tracking) return
      tracking = false
      const t = e.changedTouches[0]
      const s = swipe.current
      if (t.clientX - sx > 70 && Math.abs(t.clientY - sy) < 45 && (s.hasOverlay() || s.canBack)) {
        s.back()
      }
    }
    document.addEventListener('touchstart', onStart, { passive: true })
    document.addEventListener('touchend', onEnd, { passive: true })
    return () => {
      document.removeEventListener('touchstart', onStart)
      document.removeEventListener('touchend', onEnd)
    }
  }, [])

  // The root background paints the whole window — including any strip the layout
  // viewport doesn't cover on a letterboxed iOS install. Match it to the screen in
  // front of it (dark on the sign-in screen, the tab bar's colour in the app) so
  // that strip reads as part of the design instead of a white band.
  useEffect(() => {
    document.documentElement.classList.toggle('on-auth', !user)
    return () => document.documentElement.classList.remove('on-auth')
  }, [user])

  // Publish the tab bar's real height so screens can reserve exactly that much
  // space. It changes with the size/orientation breakpoints (icon-only in
  // landscape, compact on short screens), so it's measured rather than assumed.
  const tabbar = useRef<HTMLElement>(null)
  useEffect(() => {
    const el = tabbar.current
    // No bar on a drill-down screen: report 0 so content padding and the FAB
    // reclaim that space instead of leaving a mystery gap at the bottom.
    if (!el) {
      document.documentElement.style.setProperty('--tabbar-h', '0px')
      return
    }
    const publish = () => document.documentElement.style
      .setProperty('--tabbar-h', `${Math.round(el.getBoundingClientRect().height)}px`)
    publish()
    const ro = new ResizeObserver(publish)
    ro.observe(el)
    window.addEventListener('orientationchange', publish)
    return () => { ro.disconnect(); window.removeEventListener('orientationchange', publish) }
  }, [user, route])

  // Park the bar on the bottom of whatever is actually VISIBLE, re-measured live.
  // visualViewport describes the region not covered by browser chrome, so this
  // follows a toolbar appearing or collapsing instead of assuming a fixed offset.
  // Anything CSS units get wrong (iOS mis-reports 100dvh in an installed PWA) is
  // bypassed, because this is a measurement rather than a calculation.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const place = () => {
      const layout = document.documentElement.clientHeight
      const hidden = Math.max(0, Math.round(layout - (vv.offsetTop + vv.height)))
      document.documentElement.style.setProperty('--chrome-bottom', `${hidden}px`)
    }
    place()
    vv.addEventListener('resize', place)
    vv.addEventListener('scroll', place)
    window.addEventListener('orientationchange', place)
    return () => {
      vv.removeEventListener('resize', place)
      vv.removeEventListener('scroll', place)
      window.removeEventListener('orientationchange', place)
    }
  }, [user])

  // Repair a push subscription that has gone stale (cache cleared, app reinstalled,
  // iOS dropped it). Silent when nothing is wrong, and needs no permission prompt
  // because it only acts when permission was already granted.
  useEffect(() => {
    if (!user) return
    let cancelled = false
    const t = window.setTimeout(async () => {
      try {
        const s = await getSettings()
        if (cancelled || !s.available || !s.enabled) return
        await syncSubscription(s.publicKey)
      } catch { /* offline, or push not set up — nothing to repair */ }
    }, 2500) // let the first screen paint before touching the service worker
    return () => { cancelled = true; window.clearTimeout(t) }
  }, [user])

  // Check for a newer version on open. Silent on the publisher (it updates from
  // source → available:false) and offline; only surfaces the banner on a customer
  // copy that has an update they have not already dismissed.
  useEffect(() => {
    if (!user) return
    let live = true
    api<{ available: boolean; version?: string }>('/api/update')
      .then((r) => {
        if (!live || !r.available || !r.version) return
        if (localStorage.getItem('update.dismissed') !== r.version) setUpd({ version: r.version })
      })
      .catch(() => { /* offline / not applicable — never a red screen */ })
    return () => { live = false }
  }, [user])

  // Bring a focused field into view once the keyboard has finished animating.
  // Applies app-wide, so full-screen forms (login, scan details) behave the same
  // as sheets; without it a field low on a long form sits under the keyboard.
  useEffect(() => {
    const onFocus = (e: FocusEvent) => {
      const t = e.target as HTMLElement | null
      if (!t?.matches?.('input, select, textarea')) return
      window.setTimeout(() => t.scrollIntoView({ block: 'center', behavior: 'smooth' }), 280)
    }
    document.addEventListener('focusin', onFocus)
    return () => document.removeEventListener('focusin', onFocus)
  }, [])

  if (!ready) return <div className="app"><div className="spinner" /></div>
  if (!user) return <div className="app auth-mode"><ConnectionBanner /><Login /></div>
  // A closed licence gate (402) takes over the whole app: every guarded screen
  // would only 402 anyway. The customer is signed in by now — activation needs
  // an account — so this sits after the login check, not before it.
  if (licenceBlock) return <div className="app auth-mode"><ConnectionBanner /><Activation /></div>

  const Screen = SCREENS[route] || Dashboard
  const tabs = TABS.filter((t) => !t.mod || can(t.mod))
  const activeTab = tabs.find((t) => t.key === route)?.key || ''

  return (
    <div className="app">
      <Sidebar />
      <div className="content">
      <ConnectionBanner />
      {upd && (
        <div className="update-banner">
          <span className="ub-txt">🎉 Version {upd.version} is available</span>
          <button className="btn sm" onClick={() => { go('profile'); setUpd(null) }}>Update now</button>
          <button className="ub-x" aria-label="Dismiss"
            onClick={() => { localStorage.setItem('update.dismissed', upd.version); setUpd(null) }}>×</button>
        </div>
      )}
      {/* Above the screen, not inside the Gallery. Backing up a phone's library
          takes many minutes and nobody sits on one screen for that long -- moving
          to Expenses used to hide the progress entirely while the upload carried
          on, which reads as the upload having stopped. It renders nothing when
          there is nothing to upload. */}
      <UploadBar />
      <Screen />
      </div>
      {/* Portalled to <body>. As a child of .app it was clipped by that element's
          overflow:hidden and height — WebKit clips position:fixed descendants in
          that case — which left the bar short of the screen bottom. Outside .app
          nothing can constrain it, so bottom:0 really is the bottom. */}
      {createPortal(
        <nav className="tabbar" ref={tabbar}>
        {tabs.map((t) => (
          <button key={t.key} className={`tab ${activeTab === t.key ? 'on' : ''}`} onClick={() => go(t.key)}>
            <span className="tab-ic">
              <t.Icon />
              {t.key === 'modules' && attnTotal > 0 && <span className={`tab-badge${attnTotal >= 5 ? ' pulse' : ''}`}>{attnTotal > 9 ? '9+' : attnTotal}</span>}
            </span>
            <span className="tab-label">{t.label}</span>
          </button>
        ))}
        </nav>,
        document.body,
      )}
    </div>
  )
}
