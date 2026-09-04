// Auth context: holds the session, restores it on load, exposes login/logout + RBAC helper.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, tokenStore, onUnauthorized, onLicenceBlocked, onStorageBlocked,
         type LicenceBlock, type StorageBlock } from './api'
import { uploadDB } from './uploadDB'
import type { ModuleKey, Session, User } from './types'

// Signing out has to remove the DATA too, not just the token. The service worker
// caches every /api response and IndexedDB holds queued photo blobs — both survive
// a token wipe and are readable from devtools by whoever picks up the device next.
async function purgeLocalData() {
  try {
    if ('caches' in window) {
      const keys = await caches.keys()
      await Promise.all(keys.map((k) => caches.delete(k)))
    }
  } catch { /* best effort */ }
  try { await uploadDB.clearAll() } catch { /* best effort */ }
}

interface AuthState {
  user: User | null
  modules: ModuleKey[]
  ready: boolean
  /** Set when the server answers 402: this copy's licence needs activating. */
  licenceBlock: LicenceBlock | null
  clearLicenceBlock: () => void
  /** Set when the server answers 503: the folder the records live in cannot be
   *  read. Held separately from licenceBlock because it must WIN over it — an
   *  unreadable folder cannot yield a licence either, and reporting the licence
   *  sends the owner hunting for a file that is fine, on the failed disk. */
  storageBlock: StorageBlock | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  /** Re-pull the signed-in user after a profile change (name, avatar). */
  refreshUser: () => Promise<void>
  can: (m: ModuleKey) => boolean
}

const Ctx = createContext<AuthState>(null!)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [modules, setModules] = useState<ModuleKey[]>([])
  const [ready, setReady] = useState(false)
  const [licenceBlock, setLicenceBlock] = useState<LicenceBlock | null>(null)
  const [storageBlock, setStorageBlock] = useState<StorageBlock | null>(null)

  useEffect(() => {
    // A 401 means the session died (expired, revoked, password changed elsewhere) —
    // treat it exactly like a sign-out and drop the cached data with it.
    onUnauthorized.handler = () => { setUser(null); setModules([]); purgeLocalData() }
    // A 402 means the licence gate is closed — show the activation screen. Fires
    // from anywhere a guarded request lands, so one closed gate surfaces once.
    onLicenceBlocked.handler = (info) => setLicenceBlock(info)
    // A 503 carrying a storage fault means the records folder is unreachable.
    onStorageBlocked.handler = (info) => setStorageBlock(info)
    const t = tokenStore.get()
    if (!t) { setReady(true); return }
    api<{ user: User; modules: ModuleKey[] }>('/api/auth/me')
      .then(({ user, modules }) => { setUser(user); setModules(modules) })
      .catch(() => tokenStore.clear())
      .finally(() => setReady(true))
  }, [])

  async function login(email: string, password: string) {
    const s = await api<Session>('/api/auth/login', { method: 'POST', body: { email, password }, auth: false })
    tokenStore.set(s.token)
    setUser(s.user)
    setModules(s.modules)
  }

  function logout() {
    tokenStore.clear()
    setUser(null)
    setModules([])
    purgeLocalData()
  }

  async function refreshUser() {
    const { user, modules } = await api<{ user: User; modules: ModuleKey[] }>('/api/auth/me')
    setUser(user)
    setModules(modules)
  }

  const can = (m: ModuleKey) => user?.role === 'admin' || modules.includes(m)
  const clearLicenceBlock = () => setLicenceBlock(null)

  return (
    <Ctx.Provider value={{ user, modules, ready, licenceBlock, clearLicenceBlock, storageBlock, login, logout, refreshUser, can }}>
      {children}
    </Ctx.Provider>
  )
}

export const useAuth = () => useContext(Ctx)
