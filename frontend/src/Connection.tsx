// Global connection banner.
//
// Two different failures look identical to a user but need different wording:
// the phone has no internet, or the internet is fine but the app server (a PC
// at home) is off. Both are reported here in one place, rather than every screen
// inventing its own error text.
//
// While disconnected it re-probes with a backoff and clears itself the moment the
// server answers, so the user never has to guess whether it is safe to retry.
import { useCallback, useEffect, useRef, useState } from 'react'
import { connection, ping } from './api'
import { appName } from './branding'

const RETRY_MS = [3000, 5000, 10000, 20000, 30000]

export function ConnectionBanner() {
  const [down, setDown] = useState(false)
  const [offline, setOffline] = useState(typeof navigator !== 'undefined' && navigator.onLine === false)
  const [checking, setChecking] = useState(false)
  const attempt = useRef(0)
  const timer = useRef(0)

  const probe = useCallback(async (manual = false) => {
    if (manual) setChecking(true)
    const ok = await ping()
    if (manual) setChecking(false)
    if (ok) { attempt.current = 0; setDown(false) }
    return ok
  }, [])

  // Subscribe to failures reported by any API call.
  useEffect(() => {
    const unsub = connection.subscribe((ok) => setDown(!ok))
    return () => { unsub() }
  }, [])

  // Device-level radio changes are immediate and reliable — use them directly.
  useEffect(() => {
    const on = () => { setOffline(false); probe() }
    const off = () => { setOffline(true); setDown(true) }
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off) }
  }, [probe])

  // While down, keep checking with a widening gap so a recovered server is picked
  // up automatically instead of waiting for the user to poke it.
  useEffect(() => {
    if (!down || offline) return
    const wait = RETRY_MS[Math.min(attempt.current, RETRY_MS.length - 1)]
    timer.current = window.setTimeout(() => { attempt.current++; probe() }, wait)
    return () => clearTimeout(timer.current)
  }, [down, offline, probe, checking])

  // Re-check as soon as the app comes back to the foreground.
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible' && (down || offline)) probe() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [down, offline, probe])

  if (!down && !offline) return null

  return (
    <div className="conn-banner" role="status" aria-live="polite">
      <span className="conn-dot" />
      <div className="conn-text">
        <b>{offline ? 'No internet connection' : 'Can’t reach the server'}</b>
        <span>
          {offline
            ? 'You’re seeing the last data loaded. Changes can’t be saved right now.'
            : `${appName()}’s server may be switched off. Showing the last data loaded.`}
        </span>
      </div>
      <button className="conn-retry" onClick={() => probe(true)} disabled={checking}>
        {checking ? '…' : 'Retry'}
      </button>
    </div>
  )
}
