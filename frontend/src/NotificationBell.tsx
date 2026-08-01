// Bell icon with an unread badge, for a screen's top bar.
//
// Polls a deliberately cheap count endpoint. The badge is what makes the in-app
// list discoverable — without it nobody would think to look, and a dropped push
// would still read as "nothing happened".
import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useNav } from './nav'

const POLL_MS = 60_000

export function NotificationBell() {
  const { go } = useNav()
  const [unread, setUnread] = useState(0)

  const refresh = useCallback(() => {
    api<{ unread: number }>('/api/notifications/unread')
      .then((d) => setUnread(d.unread))
      .catch(() => { /* the connection banner already reports being offline */ })
  }, [])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    // Coming back to the app is the moment a new alert is most likely waiting.
    const onShow = () => { if (document.visibilityState === 'visible') refresh() }
    document.addEventListener('visibilitychange', onShow)
    return () => { window.clearInterval(id); document.removeEventListener('visibilitychange', onShow) }
  }, [refresh])

  return (
    <button className="bell" onClick={() => go('notifications')}
      aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.7 21a2 2 0 0 1-3.4 0" />
      </svg>
      {unread > 0 && <span className="bell-badge">{unread > 9 ? '9+' : unread}</span>}
    </button>
  )
}
