// App-level "needs attention" total (unpaid bills + overdue items), summed from the
// dashboard's moduleAttention map. Powers the rolled-up badge on the Modules tab.
// Refetches on login and whenever the route changes; mutating screens can also call
// refresh() for an instant update.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, tokenStore } from './api'
import { useAuth } from './auth'
import { useNav } from './nav'
import type { DashboardData } from './types'

interface Attn { total: number; refresh: () => void }
const Ctx = createContext<Attn>({ total: 0, refresh: () => {} })

export function AttentionProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const { route } = useNav()
  const [total, setTotal] = useState(0)

  const refresh = useCallback(() => {
    if (!tokenStore.get()) return
    api<DashboardData>('/api/dashboard')
      .then((d) => setTotal(Object.values(d.moduleAttention || {}).reduce((s, n) => s + (n || 0), 0)))
      .catch(() => {})
  }, [])

  useEffect(() => { if (user) refresh(); else setTotal(0) }, [user, route, refresh])

  return <Ctx.Provider value={{ total, refresh }}>{children}</Ctx.Provider>
}

export const useAttention = () => useContext(Ctx)
