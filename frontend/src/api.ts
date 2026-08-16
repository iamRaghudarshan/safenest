// Thin fetch wrapper: attaches the JWT, unwraps JSON, and turns every failure —
// including a dead network or an unreachable server — into a typed ApiError with a
// message that is safe to show a user.

const TOKEN_KEY = 'finmate.token'

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export class ApiError extends Error {
  status: number
  /** True when the request never reached the server (no network, or the PC is off). */
  offline: boolean
  constructor(status: number, message: string, offline = false) {
    super(message)
    this.status = status
    this.offline = offline
  }
}

type Options = { method?: string; body?: unknown; auth?: boolean }

// Fired when any call returns 401 so the app can drop back to the login screen.
export const onUnauthorized = { handler: null as null | (() => void) }

/** What the backend sends alongside a 402 when the licence gate is closed. */
export type LicenceBlock = {
  state?: string
  reason?: string
  name?: string
  key_id?: string
  expires_on?: string
}
// Fired when any call returns 402 (the licence gate is closed) so the app can
// show the activation screen instead of a dead error toast on every request.
export const onLicenceBlocked = { handler: null as null | ((info: LicenceBlock) => void) }

/** Connection watchers — the banner subscribes so a failure anywhere surfaces once,
 *  globally, instead of each screen inventing its own error text. */
type ConnListener = (online: boolean) => void
const connListeners = new Set<ConnListener>()
export const connection = {
  /** Last known reachability of the backend, as opposed to the device's radio. */
  reachable: true,
  subscribe(fn: ConnListener) { connListeners.add(fn); return () => connListeners.delete(fn) },
  report(ok: boolean) {
    if (connection.reachable === ok) return
    connection.reachable = ok
    connListeners.forEach((fn) => fn(ok))
  },
}

const OFFLINE_MSG = 'You appear to be offline. Check your internet connection.'
const UNREACHABLE_MSG = 'Can’t reach the server. It may be switched off or restarting.'

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const { method = 'GET', body, auth = true } = opts
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const t = tokenStore.get()
    if (t) headers['Authorization'] = `Bearer ${t}`
  }

  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    // fetch only rejects for transport-level failures: no radio, DNS, refused
    // connection, tunnel down. Anything the server answered lands below instead.
    const isOffline = typeof navigator !== 'undefined' && navigator.onLine === false
    connection.report(false)
    throw new ApiError(0, isOffline ? OFFLINE_MSG : UNREACHABLE_MSG, true)
  }

  if (res.status === 401 && auth) {
    tokenStore.clear()
    onUnauthorized.handler?.()
  }

  let data: unknown = null
  const text = await res.text()
  if (text) {
    try { data = JSON.parse(text) } catch { data = text }
  }

  // The service worker answers 503 {offline:true} when it has no cached copy.
  if (res.status === 503 && data && typeof data === 'object' && 'offline' in data) {
    connection.report(false)
    throw new ApiError(0, OFFLINE_MSG, true)
  }

  // A gateway error means the tunnel is up but the app behind it is not.
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    connection.report(false)
    throw new ApiError(res.status, UNREACHABLE_MSG, true)
  }

  connection.report(true)

  // 402 = the licence gate is closed (missing, expired, revoked). Surface it so
  // the app can show the activation screen, then still throw so the caller
  // unwinds rather than acting on an empty body.
  if (res.status === 402) {
    const licence =
      (data && typeof data === 'object' && 'licence' in data
        ? (data as { licence?: LicenceBlock }).licence
        : undefined) || {}
    onLicenceBlocked.handler?.(licence)
  }

  if (!res.ok) {
    const detail =
      (data && typeof data === 'object' && 'detail' in data && (data as { detail: unknown }).detail) ||
      res.statusText
    throw new ApiError(res.status, readableDetail(detail, res.status))
  }
  return data as T
}

/** Turn whatever came back in `detail` into a sentence a person can read.
 *
 *  A validation failure answers with a LIST of objects, not a string, and
 *  String() on that is the literal text "[object Object]" — which is what a
 *  customer was shown when a screen posted a malformed body. It says nothing,
 *  looks like a crash, and hides the one thing that would explain it.
 *
 *  upload.tsx already had to work around the same shape for photo uploads. That
 *  fix belonged here, in the one place every screen goes through.
 */
function readableDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail) return detail
  const say = (v: unknown): string => {
    if (typeof v === 'string') return v
    if (v && typeof v === 'object') {
      const o = v as { msg?: unknown; loc?: unknown[] }
      if (typeof o.msg === 'string') {
        // loc is ["body", "name"] — the last part is the field that upset it,
        // and naming it is the difference between "invalid" and "which one?".
        const field = Array.isArray(o.loc) ? o.loc[o.loc.length - 1] : undefined
        return field && field !== 'body' ? `${field}: ${o.msg}` : o.msg
      }
    }
    return ''
  }
  if (Array.isArray(detail)) {
    const parts = detail.map(say).filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  const one = say(detail)
  if (one) return one
  // Never fall through to String(object). A status code is not much, but it is
  // honest, and "[object Object]" is not even that.
  return status === 422 ? 'The app sent something this screen could not accept'
                        : `Request failed (${status})`
}

/** Cheap reachability probe used by the reconnect banner. */
export async function ping(): Promise<boolean> {
  try {
    const r = await fetch('/api/health', { cache: 'no-store' })
    const ok = r.ok
    connection.report(ok)
    return ok
  } catch {
    connection.report(false)
    return false
  }
}

/** Human-readable message for any thrown value. */
export function errorMessage(e: unknown, fallback = 'Something went wrong'): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof TypeError) return UNREACHABLE_MSG
  return e instanceof Error && e.message ? e.message : fallback
}
