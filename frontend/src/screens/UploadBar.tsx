/** Upload progress, in the corner rather than across the top.
 *
 *  WHAT WAS WRONG WITH THE OLD ONE
 *  It was a full-width sticky slab. On a 1440px window that made a 7px
 *  progress line stretch across the whole screen with the Pause and Cancel
 *  buttons stranded a foot away from the title they belonged to, and it
 *  pushed the entire page down — so starting an upload shoved the gallery
 *  out from under the pointer, while the heading underneath still read
 *  "0 photos".
 *
 *  It also never said WHICH file. "Uploading 4 of 14" over a wall of white
 *  is a progress bar; a thumbnail of the photo currently going up is the app
 *  telling you it has the thing you just picked.
 *
 *  So: a fixed card in the corner, sized to its content, that overlays
 *  instead of displacing. Bottom-right on a desktop, and full width along the
 *  bottom on a phone where a corner card would be a cramped island.
 */
import { useEffect, useRef, useState } from 'react'
import { useUpload } from '../upload'

export function UploadBar() {
  const u = useUpload()
  const [thumb, setThumb] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const lastBlob = useRef<Blob | null>(null)

  // One object URL at a time, revoked when it is replaced or when the bar
  // goes away. A URL per file with no revoke holds every uploaded photo in
  // memory until the tab is closed, which on a 2,000-photo import is the
  // whole import.
  useEffect(() => {
    const blob = u.currentBlob
    if (!blob) return
    if (blob === lastBlob.current) return
    lastBlob.current = blob
    const url = URL.createObjectURL(blob)
    setThumb((old) => {
      if (old) URL.revokeObjectURL(old)
      return url
    })
  }, [u.currentBlob])

  useEffect(() => () => { if (thumb) URL.revokeObjectURL(thumb) }, [thumb])

  const finished = u.pending === 0 && u.active === 0 && (u.done + u.failed) >= u.total

  // Clear itself once it has nothing left to say. A success banner that waits
  // to be dismissed is a success banner that is still there tomorrow; a
  // failure is NOT auto-dismissed, because that is the one the person has to
  // read and act on.
  useEffect(() => {
    if (!finished || u.failed > 0) return
    const t = window.setTimeout(() => setDismissed(true), 4000)
    return () => window.clearTimeout(t)
  }, [finished, u.failed])

  useEffect(() => { if (u.total > 0 && !finished) setDismissed(false) }, [u.total, finished])

  if (u.total === 0 || dismissed) return null

  const pct = u.total ? Math.round((u.done / u.total) * 100) : 0
  const added = u.done - u.dupes

  const title = finished
    ? u.failed
      ? `${added} uploaded, ${u.failed} could not be sent`
      : u.dupes
        ? `${added} added, ${u.dupes} already here`
        : u.done === 1 ? 'Photo uploaded' : `${u.done} photos uploaded`
    : u.offline ? 'Waiting for a connection'
      : u.paused ? 'Paused'
        : `Uploading ${Math.min(u.done + 1, u.total)} of ${u.total}`

  // The filename, not a second count. "6 pending" alongside "4 of 14" was
  // three numbers describing one thing, and they did not obviously add up
  // (the four in flight are neither done nor pending).
  const sub = finished
    ? null
    : u.offline ? 'It will carry on by itself when you are back online'
      : u.currentName || `${pct}%`

  const state = finished ? (u.failed ? 'err' : 'ok') : u.paused ? 'hold' : ''

  return (
    <div className={`upcard ${state}`} role="status" aria-live="polite">
      <div className="upcard-row">
        <div className="upcard-thumb">
          {thumb && !finished
            ? <img src={thumb} alt="" />
            : <span className="upcard-glyph">{finished ? (u.failed ? '!' : '✓') : '↑'}</span>}
          {!finished && !u.paused && !u.offline && <span className="upcard-ring" />}
        </div>

        <div className="upcard-text">
          <div className="upcard-title">{title}</div>
          {sub && <div className="upcard-sub">{sub}</div>}
        </div>

        <div className="upcard-acts">
          {!finished && (u.paused
            ? <button className="upcard-btn" onClick={u.resume} title="Resume" aria-label="Resume">▶</button>
            : <button className="upcard-btn" onClick={u.pause} title="Pause" aria-label="Pause">❚❚</button>)}
          {finished && u.failed > 0 && (
            <button className="upcard-btn wide" onClick={u.retryFailed}>Retry</button>
          )}
          <button className="upcard-btn" title={finished ? 'Dismiss' : 'Cancel'}
            aria-label={finished ? 'Dismiss' : 'Cancel'}
            onClick={() => { if (finished) setDismissed(true); else u.cancelAll() }}>✕</button>
        </div>
      </div>

      {!finished && (
        <div className="upcard-track"><i style={{ width: `${pct}%` }} /></div>
      )}

      {/* "3 failed" on its own leaves someone with nowhere to go. The server
          already said why — an unreadable file, a format it won't take, no
          room left — so show that instead of making them guess. */}
      {u.failed > 0 && u.reasons.length > 0 && (
        <div className="upcard-why">
          {u.reasons.slice(0, 3).map((r) => <div key={r}>{r}</div>)}
        </div>
      )}
    </div>
  )
}
