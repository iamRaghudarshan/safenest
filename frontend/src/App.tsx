import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useAuth } from './auth'
import { useNav } from './nav'
import { useAttention } from './attention'
import { ConnectionBanner } from './Connection'
import { getSettings, syncSubscription } from './notifications'
import Login from './screens/Login'
import Dashboard from './screens/Dashboard'
import Modules from './screens/Modules'
import Loans from './screens/Loans'
import Cards from './screens/Cards'
import Insurance from './screens/Insurance'
import Investments from './screens/Investments'
import Expenses from './screens/Expenses'
import Reminders from './screens/Reminders'
import Todos from './screens/Todos'
import Vault from './screens/Vault'
import Gallery from './screens/Gallery'
import Documents from './screens/Documents'
import Masters from './screens/Masters'
import Profile from './screens/Profile'
import Activity from './screens/Activity'
import Notifications from './screens/Notifications'
import Admin from './screens/Admin'
import Licences from './screens/Licences'
import Search from './screens/Search'
import { IcHome, IcModules, IcBell, IcUser, IcWallet, IcImage } from './icons'
import type { ModuleKey } from './types'

const SCREENS: Record<string, () => React.ReactElement> = {
  home: Dashboard, modules: Modules, reminders: Reminders, profile: Profile,
  loans: Loans, cards: Cards, insurance: Insurance, investments: Investments,
  expenses: Expenses, todo: Todos, vault: Vault, gallery: Gallery, documents: Documents,
  masters: Masters, admin: Admin, activity: Activity, notifications: Notifications,
  licences: Licences, search: Search,
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

export default function App() {
  const { user, ready, can } = useAuth()
  const { route, go, back, canBack, hasOverlay } = useNav()
  const { total: attnTotal } = useAttention()

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

  const Screen = SCREENS[route] || Dashboard
  const tabs = TABS.filter((t) => !t.mod || can(t.mod))
  const activeTab = tabs.find((t) => t.key === route)?.key || ''

  return (
    <div className="app">
      <ConnectionBanner />
      <Screen />
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
