import { useState } from 'react'
import { useAuth } from '../auth'
import { ApiError, api } from '../api'
import { useBranding } from '../branding'

/** Shown when the licence gate is closed (a generic download awaiting its key,
 *  or a copy whose licence lapsed). The customer pastes the signed key they were
 *  sent; the server verifies its signature before installing it. This is the
 *  customer-facing counterpart to the admin Licences screen — do not merge them.
 *
 *  A copy can be blocked while the person is signed in (login stays reachable
 *  through the gate), so this renders over the app, not only before sign-in. */
export default function Activation() {
  const { licenceBlock, clearLicenceBlock, logout } = useAuth()
  const brand = useBranding()
  const [key, setKey] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const state = licenceBlock?.state || ''
  // A lapsed copy (expired/revoked) means something different to the customer
  // than a fresh download that has never been activated (missing).
  const lapsed = state === 'expired' || state === 'revoked'

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      await api('/api/licence/activate', { method: 'POST', body: { token: key.trim() } })
      clearLicenceBlock()
      // Reload so auth/me and every screen re-fetch against the now-open gate,
      // rather than trying to unwind the requests that were mid-flight when it
      // was closed.
      window.location.reload()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-bg" aria-hidden="true">
        <span className="orb o1" /><span className="orb o2" /><span className="orb o3" />
      </div>

      <div className="auth-inner">
        <div className="auth-brand">
          <div className="auth-logo">
            {brand.icon_version > 0
              ? <img src={brand.icons['192']} alt="" className="auth-logo-img" />
              : '₹'}
          </div>
          <h1 className="auth-title">{brand.app_name}</h1>
          <p className="auth-tag">
            {lapsed ? 'This copy needs a current licence to continue.'
                    : 'Activate this copy to get started.'}
          </p>
        </div>

        <form onSubmit={submit} className="auth-card">
          {licenceBlock?.reason && <div className="auth-err">{licenceBlock.reason}</div>}
          <div className="field">
            <label>Licence key</label>
            <textarea className="input" rows={4} placeholder="Paste the licence key you were sent"
              value={key} onChange={(e) => setKey(e.target.value)} required autoFocus
              style={{ resize: 'vertical', fontFamily: 'monospace', wordBreak: 'break-all' }} />
          </div>
          {err && <div className="auth-err">{err}</div>}
          <button className="btn block auth-btn" disabled={busy || !key.trim()}>
            {busy ? 'Activating…' : 'Activate →'}
          </button>
        </form>

        <div className="auth-foot">
          Your key came with your download. Signed out?{' '}
          <button type="button" className="linklike" onClick={logout}>Use another account</button>
        </div>
      </div>
    </div>
  )
}
