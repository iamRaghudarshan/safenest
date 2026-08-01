// In-app notification list — the reliable half of notifications.
//
// A web push is best-effort: iOS holds them under Focus, drops them silently when
// the permission has been revoked, and never tells the server either way. Every
// alert is therefore also written server-side and shown here, so nothing is missed
// just because the phone didn't buzz.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useNav } from '../nav'
import { useToast } from '../toast'
import { TopBar, Spinner, Empty, Sheet } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { fmtDateTime, fmtTime, istDayISO, todayISO } from '../format'
import type { InboxItem } from '../types'

const KIND: Record<string, { icon: string; tint: string }> = {
  digest: { icon: '🔔', tint: 'var(--c-reminders)' },
  export: { icon: '💾', tint: 'var(--c-investments)' },
  system: { icon: 'ⓘ', tint: 'var(--c-insurance)' },
}

function dayLabel(iso: string | null): string {
  if (!iso) return 'Earlier'
  const d = iso.slice(0, 10)
  if (d === todayISO()) return 'Today'
  if (d === istDayISO(-1)) return 'Yesterday'
  return fmtDateTime(iso).split(',')[0]
}

export default function Notifications() {
  const { back, canBack, go } = useNav()
  const toast = useToast()
  const [items, setItems] = useState<InboxItem[] | null>(null)
  const [unread, setUnread] = useState(0)
  const [confirmClear, setConfirmClear] = useState(false)

  const load = useCallback(async () => {
    try {
      const d = await api<{ items: InboxItem[]; unread: number }>('/api/notifications/inbox?limit=100')
      setItems(d.items)
      setUnread(d.unread)
    } catch { setItems([]) }
  }, [])
  useEffect(() => { load() }, [load])

  async function open(n: InboxItem) {
    if (!n.read) {
      setItems((xs) => xs?.map((x) => x.id === n.id ? { ...x, read: true } : x) ?? null)
      setUnread((u) => Math.max(0, u - 1))
      api(`/api/notifications/inbox/${n.id}/read`, { method: 'POST' }).catch(() => {})
    }
    // Only follow in-app routes; a stored url is data, not a place to send someone.
    const route = (n.url || '/').replace(/^\//, '')
    if (route && route !== '' && !route.includes(':')) go(route)
  }

  async function readAll() {
    try {
      await api('/api/notifications/inbox/read-all', { method: 'POST' })
      setItems((xs) => xs?.map((x) => ({ ...x, read: true })) ?? null)
      setUnread(0)
    } catch { toast('Could not mark them read') }
  }

  async function remove(n: InboxItem) {
    setItems((xs) => xs?.filter((x) => x.id !== n.id) ?? null)
    if (!n.read) setUnread((u) => Math.max(0, u - 1))
    api(`/api/notifications/inbox/${n.id}`, { method: 'DELETE' }).catch(() => {})
  }

  async function clearAll() {
    try {
      await api('/api/notifications/inbox', { method: 'DELETE' })
      setItems([]); setUnread(0); setConfirmClear(false)
      toast('Notifications cleared')
    } catch { toast('Could not clear them') }
  }

  const groups: { day: string; rows: InboxItem[] }[] = []
  for (const n of items ?? []) {
    const day = dayLabel(n.at)
    if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, rows: [n] })
    else groups[groups.length - 1].rows.push(n)
  }

  return (
    <div className="screen">
      <TopBar title="Notifications" onBack={canBack ? back : undefined}
        sub={unread ? `${unread} unread` : items?.length ? 'All read' : undefined}
        right={items?.length ? (
          <div style={{ display: 'flex', gap: 8 }}>
            {unread > 0 && <button className="btn ghost sm" onClick={readAll}>Mark all read</button>}
            <button className="btn ghost sm" onClick={() => setConfirmClear(true)}>Clear</button>
          </div>
        ) : undefined} />

      <PullToRefresh onRefresh={load}>
        {!items ? <Spinner />
          : items.length === 0
            ? <Empty icon="🔔" title="No notifications"
                hint="Daily reminders and finished exports will appear here." />
            : groups.map((g) => (
              <div key={g.day}>
                <div className="section-title">{g.day}</div>
                <div className="list">
                  {g.rows.map((n) => {
                    const k = KIND[n.kind] || KIND.system
                    return (
                      <div key={n.id} className={`card notif-item${n.read ? '' : ' unread'}`}>
                        <button className="notif-main" onClick={() => open(n)}>
                          <span className="notif-ic" style={{ background: k.tint }}>{k.icon}</span>
                          <span className="notif-body">
                            <span className="notif-title">{n.title}</span>
                            <span className="notif-text">{n.body}</span>
                            <span className="notif-meta">
                              {fmtTime(n.at) || dayLabel(n.at)}
                              {!n.pushed && ' · in-app only'}
                            </span>
                          </span>
                          {!n.read && <span className="notif-dot" aria-label="Unread" />}
                        </button>
                        <button className="notif-del" onClick={() => remove(n)}
                          aria-label="Delete notification">×</button>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
      </PullToRefresh>

      {confirmClear && (
        <Sheet title="Clear all notifications?" onClose={() => setConfirmClear(false)}>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16 }}>
            Removes every notification from this list. Nothing else is affected.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirmClear(false)}>Cancel</button>
            <button className="btn danger block" onClick={clearAll}>Clear all</button>
          </div>
        </Sheet>
      )}
    </div>
  )
}
