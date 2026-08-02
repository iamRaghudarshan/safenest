// What the app read inside a document, and the fields that follow from it.
//
// Nothing here writes on its own. The values come from a reader that is right
// most of the time, and a wrong expiry date that filled itself in is worse than
// an empty one — nobody re-checks a field they never typed.
import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from './api'
import { useToast } from './toast'
import type { DocSuggestions } from './types'
import { appName } from './branding'

const FIELD_LABEL: Record<string, string> = {
  expiry_date: 'Expires',
  issue_date: 'Issued',
  doc_number: 'Document number',
}

/** dd-mm-yyyy for display; the API speaks ISO. */
function pretty(key: string, value: string) {
  if (!key.endsWith('_date')) return value
  const [y, m, d] = value.split('-')
  return d ? `${d}-${m}-${y}` : value
}

export function DocText({ id, onApplied }: { id: number; onApplied?: () => void }) {
  const toast = useToast()
  const [s, setS] = useState<DocSuggestions | null>(null)
  const [showText, setShowText] = useState(false)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try { setS(await api<DocSuggestions>(`/api/documents/${id}/suggestions`)) }
    catch { setS(null) }
  }, [id])

  useEffect(() => { load() }, [load])

  if (!s) return null

  // Not read yet: say so plainly rather than showing an empty box that looks broken.
  if (!s.ready) {
    return (
      <div className="doctext">
        <div className="doctext-head">📄 Reading this document…</div>
        <p className="doctext-note">
          It will be read in the background shortly. Come back in a moment.
        </p>
      </div>
    )
  }

  if (!s.has_text) return null

  const fields = Object.entries(s.fields ?? {}) as [string, string][]

  async function apply(key: string, value: string) {
    setBusy(key)
    try {
      await api(`/api/documents/${id}`, { method: 'PUT', body: { [key]: value } })
      toast(`${FIELD_LABEL[key] ?? key} saved`)
      await load()
      onApplied?.()
    } catch (e) { toast(errorMessage(e, 'Could not save that')) }
    finally { setBusy('') }
  }

  async function applyAll() {
    setBusy('all')
    try {
      await api(`/api/documents/${id}`, { method: 'PUT', body: Object.fromEntries(fields) })
      toast('Details saved')
      await load()
      onApplied?.()
    } catch (e) { toast(errorMessage(e, 'Could not save')) }
    finally { setBusy('') }
  }

  return (
    <div className="doctext">
      <div className="doctext-head">📄 {appName()} read this document</div>

      {fields.length > 0 ? (
        <>
          <p className="doctext-note">
            Found these. Check them before saving — they come from reading the scan.
          </p>
          <div className="doctext-fields">
            {fields.map(([key, value]) => (
              <div key={key} className="doctext-field">
                <div>
                  <div className="doctext-k">{FIELD_LABEL[key] ?? key}</div>
                  <div className="doctext-v">{pretty(key, value)}</div>
                </div>
                <button className="btn ghost sm" disabled={!!busy}
                  onClick={() => apply(key, value)}>
                  {busy === key ? '…' : 'Use'}
                </button>
              </div>
            ))}
          </div>
          {fields.length > 1 && (
            <button className="btn primary block sm" disabled={!!busy} onClick={applyAll}
              style={{ marginTop: 10 }}>
              {busy === 'all' ? 'Saving…' : `Use all ${fields.length}`}
            </button>
          )}
        </>
      ) : (
        <p className="doctext-note">
          Nothing left to fill in — the details are already recorded.
        </p>
      )}

      {(s.amounts?.length ?? 0) > 0 && (
        <div className="doctext-amounts">
          <span className="doctext-k">Amounts found</span>
          <div>{s.amounts!.map(a => (
            <span key={a} className="doctext-amt">₹{a.toLocaleString('en-IN')}</span>
          ))}</div>
        </div>
      )}

      <button className="doctext-toggle" onClick={() => setShowText(v => !v)}>
        {showText ? 'Hide the text' : 'Show all the text'}
      </button>
      {showText && <pre className="doctext-raw">{s.text}</pre>}
    </div>
  )
}
