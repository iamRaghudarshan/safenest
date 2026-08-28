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
    // A REPLY IS PROOF, and it outranks navigator.onLine.
    //
    // `offline` starts from navigator.onLine and used to be cleared ONLY by the
    // browser's `online` event. That event does not always arrive — a Mac that
    // slept and woke, a changed network, a browser whose idea of connectivity
    // got stuck — and the flag then stayed false for ever. The banner said "No
    // internet connection" over an app that was talking to its own server on
    // 127.0.0.1 perfectly happily, and Retry could not clear it: probe() only
    // ever touched `down`. Pressing it did nothing, which is precisely how it
    // was reported.
    //
    // So a successful ping clears both. Something answered; whatever the
    // browser believes about the network, this app is reaching its server.
    if (ok) { attempt.current = 0; setDown(false); setOffline(false) }
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
  //
  // It runs while `offline` too, which it did not before. Skipping the retry
  // when the browser claims there is no network sounds like a saving -- why
  // probe with the radio off? -- but navigator.onLine is not always right, and
  // when it is wrong this was the difference between a banner that clears
  // itself in seconds and one that never clears at all. A ping against
  // 127.0.0.1 costs nothing worth saving.
  useEffect(() => {
    if (!down && !offline) return
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
