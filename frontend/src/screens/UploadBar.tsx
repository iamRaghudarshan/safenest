// Sticky upload-progress bar for the Gallery module. Reads the global upload
// manager; pins to the top while you scroll; shows live progress, offline/paused
// state, and pause/resume/retry/cancel controls.
import { useUpload } from '../upload'

export function UploadBar() {
  const u = useUpload()
  if (u.total === 0) return null

  const pct = u.total ? Math.round((u.done / u.total) * 100) : 0
  const finished = u.pending === 0 && u.active === 0 && (u.done + u.failed) >= u.total

  const added = u.done - u.dupes
  const title = finished
    ? (u.failed ? `Uploaded ${added} · ${u.failed} failed`
      : u.dupes ? `${added} added · ${u.dupes} already in library ✓`
        : `All ${u.done} uploaded ✓`)
    : u.offline ? 'Waiting for connection…'
      : u.paused ? 'Upload paused'
        : `Uploading… ${u.done} of ${u.total}`

  return (
    <div className={`upbar${finished ? (u.failed ? ' err' : ' ok') : ''}`}>
      <div className="upbar-top">
        <div className="upbar-title">
          {!finished && !u.offline && !u.paused && <span className="upbar-spin" />}
          <span>{title}</span>
        </div>
        <div className="upbar-actions">
          {!finished && (u.paused
            ? <button onClick={u.resume}>Resume</button>
            : <button onClick={u.pause}>Pause</button>)}
          {finished && u.failed > 0 && <button onClick={u.retryFailed}>Retry</button>}
          <button onClick={u.cancelAll}>{finished ? 'Dismiss' : 'Cancel'}</button>
        </div>
      </div>
      <div className="upbar-track"><i style={{ width: `${finished ? 100 : pct}%` }} /></div>
      {!finished && <div className="upbar-sub">{u.pending} pending · {pct}%{u.failed ? ` · ${u.failed} failed` : ''}</div>}
      {/* "3 failed" on its own leaves someone with nowhere to go. The server
          already said why — an unreadable file, a format it won't take, no room
          left — so show that instead of making them guess. */}
      {u.failed > 0 && u.reasons.length > 0 && (
        <div className="upbar-why">
          {u.reasons.slice(0, 3).map((r) => <div key={r}>· {r}</div>)}
        </div>
      )}
    </div>
  )
}
