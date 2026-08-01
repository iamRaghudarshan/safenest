// Progress for the background pass that reads photos for faces and for content.
//
// The work happens on the server and can take many minutes over a large library,
// so the only honest thing to do is show what it's doing and how far along it is.
// Polls quickly while running and slowly when idle, and reports what it sees to an
// optional callback so a host screen doesn't need a second poller of its own.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { useToast } from './toast'
import type { IndexStatus } from './types'
import { appName } from './branding'

const FAST_MS = 2500   // while a pass is running
const SLOW_MS = 30000  // idle: just enough to notice new uploads

const JOB_LABEL: Record<string, string> = {
  faces: 'Grouping people by face',
  clip: 'Reading photos so you can search by content',
}

export function PhotoIndexCard({ onStatus }: { onStatus?: (s: IndexStatus) => void }) {
  const toast = useToast()
  const [s, setS] = useState<IndexStatus | null>(null)
  const [busy, setBusy] = useState(false)
  // Kept in a ref so changing the callback never restarts the polling loop.
  const report = useRef(onStatus)
  report.current = onStatus

  const load = useCallback(async () => {
    try {
      const next = await api<IndexStatus>('/api/gallery/index')
      setS(next)
      report.current?.(next)
      return next
    } catch { return null }
  }, [])

  useEffect(() => {
    let alive = true
    let timer = 0
    const tick = async () => {
      const next = await load()
      if (!alive) return
      timer = window.setTimeout(tick, next?.running ? FAST_MS : SLOW_MS)
    }
    tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [load])

  async function start() {
    setBusy(true)
    try { await api('/api/gallery/index', { method: 'POST', body: {} }); await load() }
    catch { toast('Could not start') }
    finally { setBusy(false) }
  }

  async function stop() {
    setBusy(true)
    try { await api('/api/gallery/index/stop', { method: 'POST' }); await load() }
    catch { /* it stops on its own soon enough */ }
    finally { setBusy(false) }
  }

  if (!s) return null
  const anyModel = s.models.faces || s.models.clip
  const pending = (s.models.faces ? s.pending.faces : 0) + (s.models.clip ? s.pending.clip : 0)
  // Nothing installed, or nothing left to do — say nothing at all.
  if (!anyModel || (!s.running && pending === 0)) return null

  const pct = s.total ? Math.min(100, Math.round((s.done / s.total) * 100)) : 0

  return (
    <div className="card pidx">
      <div className="pidx-top">
        <span className="pidx-ic">{s.running ? '🧠' : '✨'}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="pidx-title">
            {s.running ? (JOB_LABEL[s.job] || 'Reading your photos') : 'Photos not read yet'}
          </div>
          <div className="pidx-sub">
            {s.running
              ? `${s.done.toLocaleString()} of ${s.total.toLocaleString()} · ${pct}%`
              : `${pending.toLocaleString()} photo${pending === 1 ? '' : 's'} waiting`}
          </div>
        </div>
        <button className="btn ghost sm" disabled={busy} onClick={s.running ? stop : start}>
          {busy ? '…' : s.running ? 'Pause' : 'Start'}
        </button>
      </div>

      {s.running && (
        <div className="pidx-bar"><span style={{ width: `${Math.max(2, pct)}%` }} /></div>
      )}

      <div className="pidx-foot">
        {s.running && s.job === 'faces' && s.people > 0 &&
          `${s.people.toLocaleString()} ${s.people === 1 ? 'person' : 'people'} found · `}
        {s.running && s.skipped_documents > 0 &&
          `${s.skipped_documents.toLocaleString()} document${s.skipped_documents === 1 ? '' : 's'} skipped · `}
        {s.running
          ? `Runs on the ${appName()} computer — you can close the app and it carries on.`
          : 'Runs in the background and picks up where it left off.'}
      </div>

      {s.error && <div className="pidx-err">{s.error}</div>}
    </div>
  )
}
