// Licences issued to other people, and the app builds that carry them.
//
// The whole flow lives on one screen because it is one decision: who gets a copy,
// for how long, and is it still theirs. Splitting "issue" from "build" across two
// places is how a customer ends up with a licence and no app, or an app whose
// licence was never signed.
import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from '../api'
import { useToast } from '../toast'
import { TopBar, Sheet, Field } from '../ui'
import { SettingsGroup, SettingsRow, SettingsBlock } from '../settings'
import type { BroadcastItem, Licence, LicenceList, LicenceState } from '../types'
import { appName } from '../branding'

const LOOK: Record<LicenceState, { label: string; tint: string; icon: string }> = {
  ok: { label: 'Active', tint: 'var(--ok)', icon: '🟢' },
  expiring: { label: 'Expiring', tint: 'var(--warn)', icon: '🟡' },
  grace: { label: 'Grace', tint: 'var(--warn)', icon: '🟠' },
  expired: { label: 'Expired', tint: 'var(--danger)', icon: '🔴' },
  revoked: { label: 'Withdrawn', tint: 'var(--danger)', icon: '⛔' },
  invalid: { label: 'Invalid', tint: 'var(--danger)', icon: '⚠️' },
  missing: { label: 'No licence', tint: 'var(--ink-faint)', icon: '—' },
  suspended: { label: 'Suspended', tint: 'var(--warn)', icon: '⏸️' },
}

const KIND_ICON: Record<string, string> = { update: '⬆️', news: '📣', urgent: '⚠️' }

/** Up to two letters for an avatar tile. Falls back to the key id's own letters
 *  rather than rendering an empty circle if a name is ever blank. */
function initials(name: string) {
  const parts = (name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

const PRESETS = [7, 30, 90, 365]
// Matches licence_poll_minutes on the backend — how long an instruction takes to
// reach a running copy.
const POLL_MINUTES = 15

/** A titled block, so the sheet reads as sections rather than one long list. */
function LicSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="lic-sec">
      <h3 className="lic-sec-h">{title}</h3>
      {children}
    </section>
  )
}

function LicRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="lic-row">
      <span className="lic-row-k">{k}</span>
      <span className="lic-row-v">{v ?? '—'}</span>
    </div>
  )
}

export default function Licences() {
  const toast = useToast()
  const [data, setData] = useState<LicenceList | null>(null)
  const [newOpen, setNewOpen] = useState(false)
  const [chosen, setChosen] = useState<Licence | null>(null)
  const [issued, setIssued] = useState<Licence | null>(null)
  const [news, setNews] = useState(false)
  const [rel, setRel] = useState(false)

  const load = useCallback(async () => {
    try { setData(await api<LicenceList>('/api/licences')) }
    catch (e) { toast(errorMessage(e, 'Could not load licences')) }
  }, [toast])

  useEffect(() => { load() }, [load])

  return (
    <div className="screen">
      <TopBar title="Licences" />

      <SettingsGroup title="Give someone a copy"
        footer="Each copy carries a signed licence and stops working when it expires. The person you send it to gets a single user account — never an administrator.">
        <SettingsRow icon="➕" tint="var(--brand)" label="Issue a new licence"
          sub="Name, email and how many days" onClick={() => setNewOpen(true)} />
        <SettingsRow icon="📣" tint="var(--c-reminders)" label="Tell everyone something"
          sub="New version, a change, anything — reaches every copy"
          onClick={() => setNews(true)} />
        <SettingsRow icon="⬆️" tint="var(--ok)" label="Release a new version"
          sub="Package what is built here and offer it to every customer"
          onClick={() => setRel(true)} />
        {data && (
          <SettingsRow icon="📊" tint="var(--ink-faint)" label="Issued"
            value={`${data.live} active of ${data.total}`} />
        )}
      </SettingsGroup>

      {data && data.licences.length > 0 && (
        <section className="lic-list">
          <h2 className="lic-list-h">Issued licences</h2>
          {data.licences.map(l => <LicenceCard key={l.id} l={l} onOpen={() => setChosen(l)} />)}
        </section>
      )}

      {data && data.licences.length === 0 && (
        <SettingsGroup>
          <SettingsBlock>
            <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>
              No licences yet. Issue one, then build the app for it — that copy will
              run only while the licence lasts.
            </p>
          </SettingsBlock>
        </SettingsGroup>
      )}

      {newOpen && (
        <IssueLicence hosting={data?.hosting ?? { available: false, domain: '' }}
          onClose={() => setNewOpen(false)}
          onDone={(l) => { setNewOpen(false); setIssued(l); load() }} />
      )}
      {chosen && (
        <LicenceDetail licence={chosen} onClose={() => setChosen(null)}
          onChanged={() => { setChosen(null); load() }} />
      )}
      {issued && <TokenSheet licence={issued} onClose={() => setIssued(null)} />}
      {news && <Broadcast onClose={() => setNews(false)} />}
      {rel && <Releases onClose={() => setRel(false)} />}
    </div>
  )
}

/** One issued licence, as a card rather than a settings row.
 *
 *  A settings row gives one line of subtitle, which forced status, days left,
 *  expiry date and hostname to be crammed into a single run-on string. These are
 *  four different kinds of fact and they read better separated: who it is, what
 *  state it is in, and when it runs out.
 *
 *  Deliberately no progress bar for the remaining days — the original duration is
 *  not stored, so any bar would need an invented denominator. A coloured day count
 *  says the same thing without pretending to know more than it does.
 */
function LicenceCard({ l, onOpen }: { l: Licence; onOpen: () => void }) {
  const look = LOOK[l.state] ?? LOOK.invalid
  const ended = l.state === 'revoked' || l.state === 'expired'
  const urgent = l.days_left != null && l.days_left <= 7

  return (
    <button type="button" className={`lic-card${ended ? ' ended' : ''}`} onClick={onOpen}>
      <span className="lic-card-av" style={{ background: look.tint }}>{initials(l.name)}</span>

      <span className="lic-card-body">
        <span className="lic-card-top">
          <span className="lic-card-name">{l.name}</span>
          <span className="lic-pill" style={{ color: look.tint, borderColor: look.tint }}>
            {look.label}
          </span>
        </span>

        <span className="lic-card-mail">{l.email}</span>

        <span className="lic-card-facts">
          {!ended && l.days_left != null && (
            <span className={`lic-fact days${urgent ? ' urgent' : ''}`}>
              {l.days_left} {l.days_left === 1 ? 'day' : 'days'} left
            </span>
          )}
          {l.expires_on && (
            <span className="lic-fact">{ended ? 'ended' : 'until'} {l.expires_on}</span>
          )}
          {l.hostname && (
            <span className="lic-fact">{l.hosted ? '🌐' : '⛔'} {l.hostname}</span>
          )}
          <span className="lic-fact">
            {l.checkins > 0 ? `last seen ${l.last_seen}` : 'never opened'}
          </span>
        </span>
      </span>

      <span className="lic-card-go" aria-hidden="true">›</span>
    </button>
  )
}

/** A subdomain label from a name, matching what the server would pick. */
function slug(text: string) {
  return text.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}

function IssueLicence({ hosting, onClose, onDone }: {
  hosting: { available: boolean; domain: string }
  onClose: () => void; onDone: (l: Licence) => void
}) {
  const toast = useToast()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [days, setDays] = useState(30)
  const [perpetual, setPerpetual] = useState(false)
  // How many sign-ins their household may have. 1 is the licence holder alone,
  // which is what every licence issued before this option existed allows.
  const [seats, setSeats] = useState(1)
  const [unlimitedSeats, setUnlimitedSeats] = useState(false)
  const [note, setNote] = useState('')
  const [host, setHost] = useState(false)
  const [sub, setSub] = useState('')
  const [busy, setBusy] = useState(false)

  // Follows the name until the admin types their own, then stops fighting them.
  const [subEdited, setSubEdited] = useState(false)
  const label = subEdited ? slug(sub) : slug(name) || slug(email.split('@')[0] || '')
  const subBad = host && !/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(label)
    ? 'Letters, numbers and hyphens only' : ''

  const nameBad = name.trim().length < 2 ? 'Enter their name' : ''
  const mailBad = !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim()) ? 'Enter a valid email' : ''
  // Only checked when it applies: a perpetual licence has no number of days to be
  // wrong about, and a disabled field must not block the form.
  const daysBad = !perpetual && !(days >= 1 && days <= 3650)
    ? 'Between 1 and 3650 days' : ''
  const seatsBad = !unlimitedSeats && !(seats >= 1 && seats <= 50)
    ? 'Between 1 and 50 people' : ''

  async function submit() {
    setBusy(true)
    try {
      const l = await api<Licence>('/api/licences', {
        method: 'POST',
        body: {
          name: name.trim(), email: email.trim(), note: note.trim(),
          perpetual, days: perpetual ? undefined : days,
          unlimited_seats: unlimitedSeats, seats: unlimitedSeats ? undefined : seats,
          hosting: host, subdomain: host ? label : undefined,
        },
      })
      toast(l.hostname ? `Issued — ${l.hostname}`
        : l.perpetual ? 'Licence issued — never expires'
          : `Licence issued — ${l.days_left} days`)
      onDone(l)
    } catch (e) { toast(errorMessage(e, 'Could not issue the licence')) }
    finally { setBusy(false) }
  }

  return (
    <Sheet title="Issue a licence" onClose={onClose}>
      <Field label="Their name">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Full name" autoFocus maxLength={120} />
      </Field>
      {name && nameBad && <p className="form-hint warn">{nameBad}</p>}

      <Field label="Their email">
        <input className="input" type="email" value={email} autoComplete="off"
          onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
      </Field>
      {email && mailBad
        ? <p className="form-hint warn">{mailBad}</p>
        : <p className="form-hint">They sign in with this, and it is written into the
          licence itself — the copy cannot be set up under a different name.</p>}

      <label className="lbl" style={{ marginTop: 4 }}>Valid for</label>
      <div className="lic-presets">
        {PRESETS.map(d => (
          <button key={d} type="button"
            className={`chip${!perpetual && days === d ? ' on' : ''}`}
            onClick={() => { setPerpetual(false); setDays(d) }}>
            {d === 365 ? '1 year' : `${d} days`}
          </button>
        ))}
        <button type="button" className={`chip${perpetual ? ' on' : ''}`}
          onClick={() => setPerpetual(true)}>Never expires</button>
      </div>
      {perpetual ? (
        <p className="form-hint">
          Sold outright — this copy keeps working with no end date. You can still
          withdraw it later if you need to.
        </p>
      ) : (
        <>
          <Field label="Or exactly this many days">
            <input className="input" type="number" inputMode="numeric" min={1} max={3650}
              value={days} onChange={(e) => setDays(Number(e.target.value) || 0)} />
          </Field>
          {daysBad && <p className="form-hint warn">{daysBad}</p>}
        </>
      )}

      <label className="lbl" style={{ marginTop: 4 }}>People who can sign in</label>
      <div className="lic-presets">
        {[1, 2, 4, 6].map(n => (
          <button key={n} type="button"
            className={`chip${!unlimitedSeats && seats === n ? ' on' : ''}`}
            onClick={() => { setUnlimitedSeats(false); setSeats(n) }}>
            {n === 1 ? 'Just them' : `${n} people`}
          </button>
        ))}
        <button type="button" className={`chip${unlimitedSeats ? ' on' : ''}`}
          onClick={() => setUnlimitedSeats(true)}>No limit</button>
      </div>
      {unlimitedSeats ? (
        <p className="form-hint">
          They can add as many family sign-ins as they like.
        </p>
      ) : (
        <>
          <Field label="Or exactly this many people">
            <input className="input" type="number" inputMode="numeric" min={1} max={50}
              value={seats} onChange={(e) => setSeats(Number(e.target.value) || 0)} />
          </Field>
          {seatsBad && <p className="form-hint warn">{seatsBad}</p>}
        </>
      )}
      <p className="form-hint">
        Counts everyone signing in to their copy, themselves included. Each person's
        records stay their own. The limit travels inside the signed licence, so it
        cannot be raised on their machine.
      </p>

      {hosting.available && (
        <>
          <label className="lbl" style={{ marginTop: 4 }}>Web address</label>
          <label className="lic-check">
            <input type="checkbox" checked={host} onChange={(e) => setHost(e.target.checked)} />
            <span>Give them their own address on {hosting.domain}</span>
          </label>
          {host && (
            <>
              <div className="lic-host">
                <input className="input" value={subEdited ? sub : label}
                  onChange={(e) => { setSubEdited(true); setSub(e.target.value) }}
                  placeholder="their-name" />
                <span className="lic-host-suffix">.{hosting.domain}</span>
              </div>
              {subBad
                ? <p className="form-hint warn">{subBad}</p>
                : <p className="form-hint">
                  Created when you issue this. Their app arrives already pointed at it —
                  they never see Cloudflare, and never paste a token.
                </p>}
            </>
          )}
        </>
      )}

      <Field label="Note (optional)">
        <input className="input" value={note} maxLength={200}
          onChange={(e) => setNote(e.target.value)} placeholder="Trial, invoice no., …" />
      </Field>
      <p className="form-hint">For your own reference only — the customer never sees it.</p>

      <p className="muted" style={{ fontSize: 12.5, margin: '4px 0 14px' }}>
        The expiry counts from today, not from when they first open the app.
      </p>

      <button className="btn primary block"
        disabled={busy || !!(nameBad || mailBad || daysBad || seatsBad || subBad)}
        onClick={submit}>
        {busy ? 'Signing…' : host ? 'Issue licence and create their address' : 'Issue licence'}
      </button>
    </Sheet>
  )
}

function LicenceDetail({ licence, onClose, onChanged }: {
  licence: Licence; onClose: () => void; onChanged: () => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const [extendDays, setExtendDays] = useState(30)
  const [platform, setPlatform] = useState<'windows' | 'mac'>('windows')
  // Which platform this machine can actually produce a runnable copy for.
  const [ready, setReady] = useState<string[] | null>(null)
  useEffect(() => {
    api<{ ready_platforms: string[] }>('/api/system/export')
      .then((r) => setReady(r.ready_platforms)).catch(() => { })
  }, [])
  // Unknown until it answers — do not disable the button on a failed fetch.
  const canBuildHere = ready === null || ready.includes(platform)
  const [token, setToken] = useState<Licence | null>(null)
  const [notice, setNotice] = useState(false)
  const look = LOOK[licence.state] ?? LOOK.invalid

  async function act(what: string, path: string, body?: unknown) {
    setBusy(what)
    try {
      await api(path, { method: 'POST', body: body ?? {} })
      toast(what === 'suspend' ? 'Copy suspended'
        : what === 'unsuspend' ? 'Suspension lifted'
        : what === 'revoke' ? 'Licence withdrawn'
        : what === 'restore' ? 'Licence reinstated'
          : what === 'build' ? 'Building their app — you will be notified'
            : 'Licence extended')
      onChanged()
    } catch (e) { toast(errorMessage(e, 'That did not work')) }
    finally { setBusy('') }
  }

  async function showToken() {
    try {
      setToken(await api<Licence>(`/api/licences/${licence.id}/token`))
    } catch (e) { toast(errorMessage(e, 'Could not fetch the licence file')) }
  }

  return (
    <Sheet title={licence.name} onClose={onClose}>
      {/* One status line, not a badge floating above a wall of rows. Whether the
          copy works, and why, is the first thing anyone opens this to find out. */}
      <div className="lic-hero" style={{ borderColor: look.tint }}>
        <span className="lic-state" style={{ background: look.tint }}>{look.label}</span>
        <span className="lic-kid">{licence.key_id}</span>
        {licence.days_left != null && !licence.suspended && !licence.revoked_at && (
          <span className="lic-days">
            {licence.days_left < 0 ? 'expired' : `${licence.days_left} days left`}
          </span>
        )}
      </div>
      {(licence.suspend_reason || licence.revoke_reason) && (
        <p className="lic-why">
          {licence.revoked_at
            ? `Withdrawn ${licence.revoked_at}${licence.revoke_reason ? ` — ${licence.revoke_reason}` : ''}`
            : `Suspended ${licence.suspended_at}${licence.suspend_reason ? ` — ${licence.suspend_reason}` : ''}`}
        </p>
      )}

      <LicSection title="Customer">
        <LicRow k="Email" v={licence.email} />
        <LicRow k="Issued" v={licence.issued_on} />
        <LicRow k="Expires" v={licence.expires_on} />
        {licence.note && <LicRow k="Note" v={licence.note} />}
        {licence.hostname && (
          <LicRow k="Address" v={licence.hosted
            ? <a href={licence.url ?? undefined} target="_blank" rel="noreferrer">{licence.hostname}</a>
            : <>{licence.hostname} <span className="muted">(switched off)</span></>} />
        )}
      </LicSection>

      <LicSection title="Their copy">
        {licence.last_seen ? <>
          <LicRow k="Last seen" v={`${licence.last_seen} · ${licence.checkins} check-ins`} />
          <LicRow k="Running on" v={`${licence.last_os || '—'}${licence.last_hostname ? ` · ${licence.last_hostname}` : ''}`} />
          <LicRow k="Version" v={licence.last_version} />
          <LicRow k="IP address" v={licence.last_ip} />
        </> : (
          <p className="lic-empty">Has not checked in yet — they have not opened it with a connection.</p>
        )}
        {licence.bundle_at && <LicRow k="App built" v={licence.bundle_at} />}
      </LicSection>

      <LicSection title="Licence">
        <div className="lic-presets">
          {PRESETS.map(d => (
            <button key={d} type="button" className={`chip${extendDays === d ? ' on' : ''}`}
              onClick={() => setExtendDays(d)}>{d === 365 ? '1 year' : `${d}d`}</button>
          ))}
        </div>
        <button className="btn block" disabled={!!busy}
          onClick={() => act('extend', `/api/licences/${licence.id}/extend`, { days: extendDays })}>
          {busy === 'extend' ? 'Signing…' : `Extend to ${extendDays} days from today`}
        </button>
        <p className="lic-note">
          Signs a new licence file. Send it to them — their copy keeps the old one
          until it is replaced.
        </p>
        <button className="btn ghost block" disabled={!!busy} onClick={showToken}>
          Show the licence file
        </button>
      </LicSection>

      <LicSection title="Build their app">
        <div className="lic-presets">
          <button type="button" className={`chip${platform === 'windows' ? ' on' : ''}`}
            onClick={() => setPlatform('windows')}>🪟 Windows</button>
          <button type="button" className={`chip${platform === 'mac' ? ' on' : ''}`}
            onClick={() => setPlatform('mac')}>🍎 Mac</button>
        </div>
        <button className="btn block"
          disabled={!!busy || licence.state === 'revoked' || !canBuildHere}
          onClick={() => act('build', '/api/system/export',
            { platform, scope: 'licence', licence_id: licence.id })}>
          {busy === 'build' ? 'Starting…'
            : canBuildHere ? `Build for ${platform === 'mac' ? 'Mac' : 'Windows'}`
              : `Needs a ${platform === 'mac' ? 'Mac' : 'Windows'} computer`}
        </button>
        {/* Said before the build, not after. A compiled copy only runs on the
            system it was compiled on, and a Mac-named folder full of Windows
            binaries zips and sends perfectly happily. */}
        {!canBuildHere && (
          <p className="lic-note warn">
            There is no {platform === 'mac' ? 'Mac' : 'Windows'} build on this
            computer yet. A {platform === 'mac' ? 'Mac' : 'Windows'} executable can
            only be <em>compiled</em> on {platform === 'mac' ? 'a Mac' : 'Windows'} —
            but only once per release. Run the
            <code> Build the Mac app </code> workflow in GitHub Actions (or build it
            on {platform === 'mac' ? 'a Mac' : 'a Windows machine'}), then copy the
            folder into <code>dist-app/{platform}/</code>. After that you issue
            {platform === 'mac' ? ' Mac' : ' Windows'} copies from here like any other.
          </p>
        )}
        <p className="lic-note">
          An empty copy carrying this licence — none of your records go into it.
          {platform === 'mac' && ' Send them the .zip, not the unpacked folder.'}
        </p>
      </LicSection>

      <LicSection title="Control">
        <button className="btn ghost block" disabled={!!busy} onClick={() => setNotice(true)}>
          Send them a message
        </button>
        {licence.suspended ? (
          <button className="btn ghost block" disabled={!!busy}
            onClick={() => act('unsuspend', `/api/licences/${licence.id}/unsuspend`)}>
            {busy === 'unsuspend' ? 'Working…' : 'Lift the suspension'}
          </button>
        ) : (
          <button className="btn ghost block" disabled={!!busy}
            onClick={() => act('suspend', `/api/licences/${licence.id}/suspend`,
              { reason: 'Suspended by supplier' })}>
            {busy === 'suspend' ? 'Working…' : 'Suspend this copy'}
          </button>
        )}
        {licence.revoked_at ? (
          <button className="btn ghost block" disabled={!!busy}
            onClick={() => act('restore', `/api/licences/${licence.id}/restore`)}>
            {busy === 'restore' ? 'Working…' : 'Reinstate this licence'}
          </button>
        ) : (
          <button className="btn danger block" disabled={!!busy}
            onClick={() => act('revoke', `/api/licences/${licence.id}/revoke`,
              { reason: 'Withdrawn by supplier' })}>
            {busy === 'revoke' ? 'Working…' : 'Withdraw this licence'}
          </button>
        )}
        <p className="lic-note">
          Suspending is reversible; withdrawing is meant to be final. Either takes
          effect within {POLL_MINUTES} minutes of their copy having internet.
          {licence.hosted && ' Withdrawing also deletes their web address immediately.'}
        </p>
      </LicSection>

      {token && <TokenSheet licence={token} onClose={() => setToken(null)} />}
      {notice && <Notice licence={licence} onClose={() => setNotice(false)} />}
    </Sheet>
  )
}

/** The licence text, to be saved as data/licence.key on the customer's machine. */
function TokenSheet({ licence, onClose }: { licence: Licence; onClose: () => void }) {
  const toast = useToast()
  async function copy() {
    try {
      await navigator.clipboard.writeText(licence.token || '')
      toast('Licence copied')
    } catch { toast('Could not copy — select it and copy by hand') }
  }
  return (
    <Sheet title="Their licence file" onClose={onClose}>
      <p className="muted" style={{ fontSize: 13.5, marginBottom: 12 }}>
        Save this as <b>licence.key</b> inside their <b>data</b> folder. If you built
        their app from here it is already inside — this is for replacing an expired
        one without rebuilding.
      </p>
      <textarea className="lic-token" readOnly value={licence.token || ''} rows={7}
        onClick={(e) => e.currentTarget.select()} />
      <button className="btn primary block" onClick={copy} style={{ marginTop: 12 }}>
        Copy licence
      </button>
      <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        Anyone holding this file can run a copy licensed to {licence.name} until it
        expires. Send it the way you would send a password.
      </p>
    </Sheet>
  )
}

/** Compose a message that reaches every copy of FinMate, including ones running
 *  on machines you cannot touch.
 *
 *  Local users are notified the moment this is sent. Licensed copies are not
 *  reachable from here at all — they collect the message the next time they
 *  check in, which is why the sheet says "waiting" rather than "sent". */
function Broadcast({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [url, setUrl] = useState('')
  const [version, setVersion] = useState('')
  const [kind, setKind] = useState<'news' | 'update' | 'urgent'>('update')
  const [audience, setAudience] = useState<'all' | 'local' | 'licensed'>('all')
  const [busy, setBusy] = useState(false)
  const [past, setPast] = useState<BroadcastItem[]>([])

  const loadPast = useCallback(() => {
    api<{ items: BroadcastItem[] }>('/api/licences/broadcast')
      .then(r => setPast(r.items)).catch(() => { })
  }, [])

  useEffect(() => { loadPast() }, [loadPast])

  const bad = title.trim().length < 3 ? 'Give it a title'
    : body.trim().length < 5 ? 'Write the message' : ''

  async function send() {
    setBusy(true)
    try {
      const r = await api<{ delivered_local: number; waiting_for: number }>(
        '/api/licences/broadcast', {
        method: 'POST',
        body: {
          title: title.trim(), body: body.trim(), url: url.trim(),
          app_version: version.trim(), kind, audience,
        },
      })
      toast(`Sent to ${r.delivered_local} here · ${r.waiting_for} copies will collect it`)
      onClose()
    } catch (e) { toast(errorMessage(e, 'Could not send that')) }
    finally { setBusy(false) }
  }

  return (
    <Sheet title="Tell everyone" onClose={onClose}>
      <label className="lbl">What kind of message</label>
      <div className="lic-presets">
        {(['update', 'news', 'urgent'] as const).map(k => (
          <button key={k} type="button" className={`chip${kind === k ? ' on' : ''}`}
            onClick={() => setKind(k)}>
            {k === 'update' ? '⬆️ New version' : k === 'news' ? '📣 News' : '⚠️ Urgent'}
          </button>
        ))}
      </div>

      <label className="lbl">Who gets it</label>
      <div className="lic-presets">
        {([['all', 'Everyone'], ['licensed', 'Only customers'],
        ['local', 'Only this computer']] as const).map(([v, label]) => (
          <button key={v} type="button" className={`chip${audience === v ? ' on' : ''}`}
            onClick={() => setAudience(v)}>{label}</button>
        ))}
      </div>

      <Field label="Title">
        <input className="input" value={title} maxLength={160} autoFocus
          onChange={(e) => setTitle(e.target.value)}
          placeholder={`${appName()} 2.1 is available`} />
      </Field>

      <Field label="Message">
        <textarea className="input" rows={4} value={body} maxLength={2000}
          onChange={(e) => setBody(e.target.value)}
          placeholder="What changed, and what they should do about it." />
      </Field>

      <Field label="Link (optional)">
        <input className="input" value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="Where to download the new version" />
      </Field>

      <Field label="Version this announces (optional)">
        <input className="input" value={version} maxLength={20}
          onChange={(e) => setVersion(e.target.value)} placeholder="2.1" />
      </Field>

      {bad && <p className="form-hint warn">{bad}</p>}
      <p className="form-hint">
        People here are notified immediately. Copies on other people's machines
        collect it the next time they open {appName()} with a connection — you cannot
        push to a computer you don't run.
      </p>

      <button className="btn primary block" disabled={busy || !!bad} onClick={send}>
        {busy ? 'Sending…' : 'Send'}
      </button>

      {past.length > 0 && (
        <>
          <label className="lbl" style={{ marginTop: 20 }}>Already sent</label>
          {past.slice(0, 8).map(b => (
            <SentMessage key={b.id} item={b} onResent={loadPast} />
          ))}
        </>
      )}
    </Sheet>
  )
}

/** One sent message, and the thing that was missing: whether it actually arrived.
 *
 *  Delivery to a customer is pull-only, so "sent" and "received" are genuinely
 *  different states that can be days apart — or never close, if that person has
 *  not opened their copy yet. Showing only "sent" made a message that will never
 *  arrive look identical to one already read. */
function SentMessage({ item, onResent }: { item: BroadcastItem; onResent: () => void }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const localOnly = item.audience === 'local'
  const done = item.collected.length
  const waiting = item.waiting.length

  async function resend(onlyWaiting: boolean) {
    setBusy(true)
    try {
      const r = await api<{ delivered_local: number; waiting_for: number }>(
        `/api/licences/broadcast/${item.id}/resend`,
        { method: 'POST', body: { only_waiting: onlyWaiting } },
      )
      toast(r.waiting_for > 0
        ? `Sent again · ${r.waiting_for} ${r.waiting_for === 1 ? 'copy' : 'copies'} will collect it`
        : `Sent again to ${r.delivered_local} here`)
      onResent()
    } catch (e) { toast(errorMessage(e, 'Could not send that again')) }
    finally { setBusy(false) }
  }

  const pct = item.targets ? Math.round((done / item.targets) * 100) : 0
  const tone = waiting === 0 ? 'ok' : done === 0 ? 'wait' : 'part'

  return (
    <article className="msg">
      <header className="msg-head">
        <span className={`msg-icon ${item.kind}`}>{KIND_ICON[item.kind] ?? '📣'}</span>
        <div className="msg-headings">
          <h4 className="msg-title">{item.title}</h4>
          <p className="msg-meta">
            {item.created_at}
            {item.delivered_local > 0 && ` · ${item.delivered_local} notified here`}
          </p>
        </div>
        {item.resend_of && <span className="msg-tag">sent again</span>}
      </header>

      {localOnly ? (
        <p className="msg-note ok">Delivered on this computer.</p>
      ) : item.superseded_by ? (
        <p className="msg-note">
          Replaced by a newer send. Copies receive only the latest version, so
          nothing is outstanding here.
        </p>
      ) : item.targets === 0 ? (
        <p className="msg-note">No licensed copies to send to yet.</p>
      ) : (
        <>
          <div className="msg-progress">
            <div className="msg-bar">
              <span className={`msg-fill ${tone}`} style={{ width: `${pct}%` }} />
            </div>
            <span className={`msg-count ${tone}`}>{done} of {item.targets}</span>
          </div>
          <p className={`msg-note ${tone === 'ok' ? 'ok' : ''}`}>
            {waiting === 0
              ? 'Everyone has received this.'
              : `${waiting} ${waiting === 1 ? 'copy has' : 'copies have'} not collected it yet.`}
          </p>

          <button type="button" className="msg-toggle" aria-expanded={open}
            onClick={() => setOpen(!open)}>
            <span className={`msg-caret${open ? ' open' : ''}`}>›</span>
            {open ? 'Hide recipients' : 'Show recipients'}
          </button>

          {open && (
            <ul className="msg-people">
              {item.collected.map(r => (
                <li key={r.key_id} className="msg-person">
                  <span className="msg-av ok">{initials(r.name)}</span>
                  <span className="msg-person-n">{r.name}</span>
                  <span className="msg-person-s ok">Received</span>
                </li>
              ))}
              {item.waiting.map(r => (
                <li key={r.key_id} className="msg-person">
                  <span className="msg-av">{initials(r.name)}</span>
                  <span className="msg-person-n">{r.name}</span>
                  <span className="msg-person-s">Not opened yet</span>
                </li>
              ))}
            </ul>
          )}

          <div className="msg-actions">
            {waiting > 0 && (
              <button type="button" className="btn sm" disabled={busy}
                onClick={() => resend(true)}>
                {busy ? 'Sending…' : `Remind the ${waiting} waiting`}
              </button>
            )}
            <button type="button" className="btn ghost sm" disabled={busy}
              onClick={() => resend(false)}>
              Send to all again
            </button>
          </div>
        </>
      )}
    </article>
  )
}

/** A message for one customer. Reaches only them, on their next check-in. */
function Notice({ licence, onClose }: { licence: Licence; onClose: () => void }) {
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const bad = title.trim().length < 3 || body.trim().length < 5

  async function send() {
    setBusy(true)
    try {
      await api(`/api/licences/${licence.id}/notice`, {
        method: 'POST', body: { title: title.trim(), body: body.trim() },
      })
      toast(`Waiting for ${licence.name}`)
      onClose()
    } catch (e) { toast(errorMessage(e, 'Could not send that')) }
    finally { setBusy(false) }
  }

  return (
    <Sheet title={`Message ${licence.name}`} onClose={onClose}>
      <Field label="Title">
        <input className="input" value={title} maxLength={160} autoFocus
          onChange={(e) => setTitle(e.target.value)} placeholder="Your renewal is due" />
      </Field>
      <Field label="Message">
        <textarea className="input" rows={4} value={body} maxLength={2000}
          onChange={(e) => setBody(e.target.value)}
          placeholder="What they need to know, and what to do about it." />
      </Field>
      <p className="form-hint">
        Only {licence.name} sees this. Their copy collects it the next time it can
        reach this server — you cannot push to a computer you don't run.
      </p>
      <button className="btn primary block" disabled={busy || bad} onClick={send}>
        {busy ? 'Sending…' : 'Send'}
      </button>
    </Sheet>
  )
}

interface ReleaseRow {
  id: number; version: string; notes: string; size_mb: number
  sha256: string; is_current: boolean; published_at: string | null; available: boolean
}

/** Package what has been built here and offer it to every customer.
 *
 * Publishes the compiled folder as it stands, so what customers receive is what
 * was tested on this machine rather than a rebuild that happens to share a commit.
 * Releasing signs a manifest with the same key that signs licences; a customer's
 * copy checks that signature itself before it installs anything.
 */
function Releases({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const [rows, setRows] = useState<ReleaseRow[] | null>(null)
  const [running, setRunning] = useState('')
  const [customers, setCustomers] = useState(0)
  const [version, setVersion] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await api<{ releases: ReleaseRow[]; running: string; customers: number }>('/api/releases')
      setRows(r.releases); setRunning(r.running); setCustomers(r.customers)
    } catch (e) { toast(errorMessage(e, 'Could not load releases')) }
  }, [toast])
  useEffect(() => { load() }, [load])

  async function publish() {
    setBusy('publish')
    try {
      await api('/api/releases', { method: 'POST', body: { version: version.trim(), notes: notes.trim() } })
      setVersion(''); setNotes('')
      await load()
      toast('Packaged — now release it to your customers')
    } catch (e) { toast(errorMessage(e, 'Could not package that')) }
    finally { setBusy('') }
  }

  async function releaseAll(r: ReleaseRow) {
    if (!confirm(`Offer version ${r.version} to all ${customers} customer(s)?

Their copies will show an update they can install. Their records are not affected.`)) return
    setBusy(`rel${r.id}`)
    try {
      const out = await api<{ announced_to: number }>(`/api/releases/${r.id}/release-to-all`, { method: 'POST', body: {} })
      await load()
      toast(`Offered to ${out.announced_to} customer(s)`)
    } catch (e) { toast(errorMessage(e, 'Could not release it')) }
    finally { setBusy('') }
  }

  return (
    <Sheet title="Release a new version" onClose={onClose}>
      <SettingsGroup footer={`Packages the compiled build in dist-app as it stands. Build it first with: python packaging/build_exe.py --native`}>
        <SettingsRow icon="●" tint="var(--ink-faint)" label="This installation" value={running} />
        <SettingsRow icon="👥" tint="var(--ink-faint)" label="Live customers" value={String(customers)} />
      </SettingsGroup>

      <Field label="Version">
        <input className="input" value={version} placeholder="2.1" autoCapitalize="off"
          onChange={(e) => setVersion(e.target.value)} />
      </Field>
      <Field label="What changed">
        <textarea className="input" rows={3} value={notes} maxLength={2000}
          placeholder="Shown to customers when they are offered it"
          onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <button className="btn block" disabled={!!busy || !version.trim()} onClick={publish}>
        {busy === 'publish' ? 'Packaging…' : 'Package this build'}
      </button>

      {rows && rows.length > 0 && (
        <SettingsGroup title="Published">
          {rows.map(r => (
            <SettingsRow key={r.id} icon={r.is_current ? '🟢' : '○'}
              tint={r.is_current ? 'var(--ok)' : 'var(--ink-faint)'}
              label={`Version ${r.version}`}
              sub={`${r.size_mb} MB · ${r.published_at || ''}${r.is_current ? ' · offered to everyone' : ''}${r.available ? '' : ' · file missing'}`}
              right={r.is_current ? undefined : (
                <button className="btn ghost sm" disabled={!!busy || !r.available}
                  onClick={() => releaseAll(r)}>
                  {busy === `rel${r.id}` ? '…' : 'Release'}
                </button>
              )} />
          ))}
        </SettingsGroup>
      )}
    </Sheet>
  )
}
