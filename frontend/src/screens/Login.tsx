import { useEffect, useState } from 'react'
import { useAuth } from '../auth'
import { ApiError, api } from '../api'
import { useBranding } from '../branding'

type Addresses = { current: 'lan' | 'internet'; lan: string; public: string }

/** Lets someone signing in at home switch to the faster local address, and lets
 *  anyone confirm which way they are connected. Deliberately a set of plain links,
 *  not a fetch-and-redirect: a page served over the public https domain cannot
 *  probe an http LAN address (mixed content), so a top-level navigation is the
 *  only thing that reliably crosses between the two. The server only ever hands
 *  back the LAN address to a client already on the LAN, so this shows a switch
 *  when it has one to offer and nothing when it does not. */
function ConnectionSwitch() {
  const [addr, setAddr] = useState<Addresses | null>(null)
  useEffect(() => {
    let live = true
    api<Addresses>('/api/hosting/addresses', { auth: false })
      .then((a) => { if (live) setAddr(a) })
      .catch(() => {})            // a copy with no public address set is the norm, not an error
    return () => { live = false }
  }, [])

  if (!addr) return null
  const onLan = addr.current === 'lan'
  const other = onLan && addr.public ? addr.public : ''
  if (!onLan && !addr.public) return null

  return (
    <div className="auth-connect">
      <span className="auth-connect-now">
        {onLan ? '🏠 On your home Wi-Fi' : '🌐 Connected over the internet'}
      </span>
      {other && (
        <a className="auth-connect-alt" href={other}>Open from anywhere →</a>
      )}
    </div>
  )
}

const POINTS: [string, string][] = [
  ['🔒', 'AES-256 encrypted vault for your passwords'],
  ['🏠', 'Your records stay on your own machine'],
  ['🖼️', 'Back up your whole photo library'],
  ['📄', 'Money, documents, photos & passwords in one place'],
]

export default function Login() {
  const { login, completeTwoFactor } = useAuth()
  const brand = useBranding()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  // Set when the password was right and the account wants its second factor.
  // Holding the challenge here, rather than a "signed in but not really" flag,
  // is what keeps the half-finished state impossible to mistake for a session.
  const [challenge, setChallenge] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      const need2fa = await login(email.trim(), password)
      if (need2fa) {
        setChallenge(need2fa.challenge)
        // The password is not needed again and should not sit in memory for the
        // length of a code-entry step.
        setPassword('')
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault()
    if (!challenge) return
    setErr(''); setBusy(true)
    try {
      await completeTwoFactor(challenge, code.trim())
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : 'Something went wrong'
      setErr(msg)
      setCode('')
      // A challenge is single-use and short-lived, so once the server says it
      // expired there is nothing left to retry — send them back to the password
      // rather than letting them type codes at a dead challenge.
      if (err instanceof ApiError && /expired/i.test(err.message)) {
        setChallenge(null)
        setUseRecovery(false)
      }
    } finally {
      setBusy(false)
    }
  }

  function backToPassword() {
    setChallenge(null); setCode(''); setErr(''); setUseRecovery(false)
  }

  const Logo = (
    <div className="auth-logo">
      {brand.icon_version > 0
        ? <img src={brand.icons['192']} alt="" className="auth-logo-img" />
        : '₹'}
    </div>
  )
  const tagline = brand.tagline || 'Everything you own, kept safe at home.'

  return (
    <div className="auth login-split">
      <div className="auth-bg" aria-hidden="true">
        <span className="orb o1" /><span className="orb o2" /><span className="orb o3" />
      </div>

      {/* Brand panel — the whole left side on desktop, hidden on phones (the form
          carries a compact brand block there instead). */}
      <aside className="auth-hero">
        <div className="auth-hero-in">
          {Logo}
          <h1 className="auth-hero-title">{brand.app_name}</h1>
          <p className="auth-hero-tag">{tagline}</p>
          <ul className="auth-points">
            {POINTS.map(([ic, t]) => (
              <li key={t}><span>{ic}</span>{t}</li>
            ))}
          </ul>
        </div>
        <div className="auth-hero-foot">🔒 Secured with JWT · AES-256 vault · role-based access</div>
      </aside>

      <main className="auth-main">
        <div className="auth-inner">
          <div className="auth-brand only-mobile">
            {Logo}
            <h1 className="auth-title">{brand.app_name}</h1>
            <p className="auth-tag">{tagline}</p>
          </div>

          {challenge ? (
            <form onSubmit={submitCode} className="auth-card">
              <h2 className="auth-card-h">{useRecovery ? 'Use a recovery code' : 'Enter your code'}</h2>
              <p className="auth-card-sub">
                {useRecovery
                  ? 'One of the codes you saved when you turned on two-step sign-in. Each one works once.'
                  : `Open your authenticator app and type the six digits it shows for ${brand.app_name}.`}
              </p>
              <div className="field">
                <label>{useRecovery ? 'Recovery code' : '6-digit code'}</label>
                <input className="input" value={code} autoFocus required
                  onChange={(e) => setCode(e.target.value)}
                  // A one-time code is not a password: autocomplete="one-time-code"
                  // is what lets iOS and Android offer the code from the keyboard.
                  autoComplete={useRecovery ? 'off' : 'one-time-code'}
                  inputMode={useRecovery ? 'text' : 'numeric'}
                  placeholder={useRecovery ? 'xxxx-xxxx' : '000000'}
                  maxLength={useRecovery ? 32 : 6}
                  style={{ letterSpacing: useRecovery ? '0.05em' : '0.35em',
                           textAlign: 'center', fontSize: '1.15rem' }} />
              </div>
              {err && <div className="auth-err">{err}</div>}
              <button className="btn block auth-btn" disabled={busy || !code.trim()}>
                {busy ? 'Checking…' : 'Sign in →'}
              </button>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginTop: 12 }}>
                <button type="button" className="btn ghost sm" onClick={backToPassword}>
                  ← Back
                </button>
                <button type="button" className="btn ghost sm"
                  onClick={() => { setUseRecovery((r) => !r); setCode(''); setErr('') }}>
                  {useRecovery ? 'Use the app instead' : 'Lost your phone?'}
                </button>
              </div>
            </form>
          ) : (
          <form onSubmit={submit} className="auth-card">
            <h2 className="auth-card-h">Sign in</h2>
            <p className="auth-card-sub">Welcome back — sign in to your {brand.app_name} account.</p>
            <div className="field">
              <label>Email</label>
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
          )}

          <ConnectionSwitch />

          <div className="auth-foot only-mobile">🔒 Secured with JWT · AES-256 vault · role-based access</div>
        </div>
      </main>
    </div>
  )
}
