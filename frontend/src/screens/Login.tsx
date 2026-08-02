import { useState } from 'react'
import { useAuth } from '../auth'
import { ApiError } from '../api'
import { useBranding } from '../branding'

export default function Login() {
  const { login } = useAuth()
  const brand = useBranding()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      await login(email.trim(), password)
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
          {/* The uploaded icon when there is one; the rupee mark is the fallback
              so a copy that never set an icon still looks deliberate. */}
          <div className="auth-logo">
            {brand.icon_version > 0
              ? <img src={brand.icons['192']} alt="" className="auth-logo-img" />
              : '₹'}
          </div>
          <h1 className="auth-title">{brand.app_name}</h1>
          <p className="auth-tag">{brand.tagline || 'Your money, beautifully organised.'}</p>
        </div>

        <form onSubmit={submit} className="auth-card">
          <div className="field">
            <label>Email</label>
            {/* Not a branded example address. The old one read "you@finmate.app",
                which put a name the branding screen cannot reach on the first
                screen anybody sees — and on a renamed copy it named the wrong
                product to the customer, on their own machine. */}
            <input className="input" type="email" autoComplete="username" placeholder="you@example.com"
              value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </div>
          <div className="field">
            <label>Password</label>
            <div className="pw-wrap">
              <input className="input" type={show ? 'text' : 'password'} autoComplete="current-password" placeholder="••••••••"
                value={password} onChange={(e) => setPassword(e.target.value)} required />
              <button type="button" className="pw-toggle" onClick={() => setShow((s) => !s)}>{show ? 'Hide' : 'Show'}</button>
            </div>
          </div>
          {err && <div className="auth-err">{err}</div>}
          <button className="btn block auth-btn" disabled={busy}>{busy ? 'Signing in…' : 'Sign in →'}</button>
        </form>

        <div className="auth-foot">🔒 Secured with JWT · AES-256 vault · role-based access</div>
      </div>
    </div>
  )
}
