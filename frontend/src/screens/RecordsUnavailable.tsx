/**
 * Shown when the folder this copy keeps its records in cannot be read.
 *
 * WHY THIS SCREEN EXISTS AT ALL
 * Records can live on another disk. When that disk stops answering, everything
 * downstream fails at once and none of it names the cause: the licence file is
 * unreadable, so the app says the licence is missing; the database is
 * unreadable, so every request is a 500. A customer facing that reasonably
 * concludes the app is broken and reinstalls it — the one action that can turn a
 * recoverable drive fault into real, permanent loss.
 *
 * So this screen has one job before any of its buttons: say what is actually
 * wrong, name the disk, and state plainly that nothing has been deleted.
 */
import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../auth'

export default function RecordsUnavailable() {
  const { storageBlock } = useAuth()
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [done, setDone] = useState(false)

  const where = storageBlock?.volume || storageBlock?.folder || 'another disk'
  const reason = storageBlock?.reason || 'unreadable'

  const headline =
    reason === 'missing' ? 'Your records drive is not connected'
    : reason === 'readonly' ? 'Your records drive will not accept changes'
    : 'Your records drive cannot be read'

  const advice =
    reason === 'missing'
      ? 'Plug it back in, then press Try again.'
      : reason === 'readonly'
      ? 'It may be write-protected, or it may need reconnecting.'
      : 'Try a different cable and a different port, plugged straight into the computer rather than through a hub. Then press Try again.'

  async function retry() {
    setBusy('retry'); setNote('')
    try {
      const r = await api<{ ok: boolean; restart_required?: boolean; detail?: string }>(
        '/api/storage/retry', { method: 'POST' })
      if (r.ok) { setDone(true); setNote('Found it. Close and reopen the app to carry on.') }
      else setNote('Still cannot be read. The drive is connected but not answering.')
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That did not work.')
    } finally { setBusy('') }
  }

  async function useLocal() {
    setBusy('local'); setNote('')
    try {
      const r = await api<{ ok: boolean; kept_at?: string }>(
        '/api/storage/use-local', { method: 'POST' })
      if (r.ok) {
        setDone(true)
        setNote('Done. Close and reopen the app — it will use the records on this computer.')
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : 'That did not work.')
    } finally { setBusy('') }
  }

  return (
    <div className="auth-wrap">
      <div className="auth-card" style={{ maxWidth: 560 }}>
        <h1 style={{ marginBottom: 6 }}>{headline}</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Your records are kept on <strong>{where}</strong>.
        </p>

        {/* First, and before any button. Somebody looking at a broken app assumes
            the worst, and the assumption is what makes them reinstall. */}
        <div className="callout" style={{ margin: '14px 0' }}>
          <strong>Nothing has been deleted.</strong> Your records are still on that
          drive, exactly as they were. This copy simply cannot read them at the
          moment, so it has stopped rather than start an empty one.
        </div>

        <p>{advice}</p>

        {note && <p className={done ? 'ok-text' : 'warn-text'}>{note}</p>}

        <div style={{ display: 'flex', gap: 10, marginTop: 16, flexWrap: 'wrap' }}>
          <button className="btn primary" onClick={retry} disabled={!!busy}>
            {busy === 'retry' ? 'Checking…' : 'Try again'}
          </button>
          <button className="btn" onClick={useLocal} disabled={!!busy}>
            {busy === 'local' ? 'Switching…' : 'Use the records on this computer'}
          </button>
        </div>

        {/* The second button is the one that needs a warning, not a label. */}
        <p className="muted" style={{ fontSize: 13, marginTop: 14 }}>
          "Use the records on this computer" does not delete or move anything on
          the drive. It only stops this copy waiting for it, so you can work from
          the last copy held here. Where your records live is remembered, so you
          can switch back once the drive is readable again.
        </p>

        {storageBlock?.folder && (
          <p className="muted" style={{ fontSize: 12, marginTop: 10, wordBreak: 'break-all' }}>
            {storageBlock.folder}
            {storageBlock.detail ? ` — ${storageBlock.detail}` : ''}
          </p>
        )}
      </div>
    </div>
  )
}
