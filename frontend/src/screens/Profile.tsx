import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, errorMessage, tokenStore } from '../api'
import { useAuth } from '../auth'
import { useNav } from '../nav'
import { useToast } from '../toast'
import { TopBar, Segment, Sheet, Field } from '../ui'
import { IcLogout, IcLock } from '../icons'
import { getTheme, applyTheme, type Theme } from '../theme'
import { fmtDateTime, fmtTime } from '../format'
import { SettingsGroup, SettingsRow, SettingsBlock } from '../settings'
import { appName, brand as brandStore, useBranding, type Branding } from '../branding'
import { PhotoIndexCard } from '../PhotoIndex'
import {
  BUILD_ID, checkForUpdate, clearAppCache, formatBytes, storageInfo, type StorageInfo,
} from '../maintenance'
import type { AppHost, HostReport, StorageReport, LicenceStatus } from '../types'
import {
  blockedReason, disable as disablePush, enable as enablePush, getSettings, isStandalone,
  saveSettings, sendTest, syncSubscription, type DeviceState, type PushSettings,
} from '../notifications'

export default function Profile() {
  const { user, logout, can } = useAuth()
  const { go } = useNav()
  const [theme, setTheme] = useState<Theme>(getTheme())
  const [pwOpen, setPwOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [exportScope, setExportScope] = useState<ExportScope | null>(null)
  const [brandOpen, setBrandOpen] = useState(false)
  const [webOpen, setWebOpen] = useState(false)
  const brand = useBranding()

  function changeTheme(t: Theme) { setTheme(t); applyTheme(t) }

  return (
    <div className="screen">
      <TopBar title="Profile" />

      <button className="set-hero" onClick={() => setEditOpen(true)}>
        <Avatar size={58} />
        <div className="set-hero-main">
          <div className="set-hero-name">{user?.name}</div>
          <div className="set-hero-mail">{user?.email}</div>
          {/* What the account may do, not what its row happens to say. This is
              the badge the customer read as "admin" on a copy that is meant to
              have no administrator — and it said so while every admin call was
              already coming back 403. */}
          <span className="set-hero-role">{user?.can_admin ? 'admin' : 'user'}</span>
        </div>
        <span className="set-chev" aria-hidden="true">›</span>
      </button>

      <SettingsGroup title="Account">
        <SettingsRow icon="✏️" tint="var(--brand)" label="Edit profile"
          sub="Change your name or photo" onClick={() => setEditOpen(true)} />
        <SettingsRow icon={<IcLock className="ic" />} tint="var(--c-vault)" label="Change password"
          sub="Update your account password" onClick={() => setPwOpen(true)} />
      </SettingsGroup>

      <SettingsGroup title="Appearance">
        <SettingsBlock>
          <Segment value={theme} onChange={changeTheme}
            options={[{ value: 'light', label: '☀️ Light' }, { value: 'dark', label: '🌙 Dark' }, { value: 'system', label: '⚙️ Auto' }]} />
        </SettingsBlock>
      </SettingsGroup>

      <NotificationSettings />

      {can('gallery') && <>
        <PhotoIndexCard />
        <SettingsGroup title="Photos">
          <SettingsRow icon="🧹" tint="var(--c-gallery)" label="Find duplicate photos"
            sub="Remove exact copies" onClick={() => go('gallery', 'duplicates')} />
          <SettingsRow icon="🔍" tint="var(--c-reminders)" label="Find similar photos"
            sub="Resized, re-saved or edited copies" onClick={() => go('gallery', 'similar')} />
        </SettingsGroup>
      </>}

      <SettingsGroup title="Customization">
        <SettingsRow icon="🗂️" tint="var(--c-expenses)" label="Manage lists"
          sub="Custom categories, banks &amp; more" onClick={() => go('masters')} />
      </SettingsGroup>

      {/* Available to everyone: your own data only, on your own machine. */}
      <SettingsGroup title="My data">
        <SettingsRow icon="🔔" tint="var(--c-reminders)" label="Notifications"
          sub={`Everything ${brand.app_name} has told you`} onClick={() => go('notifications')} />
        <SettingsRow icon="📋" tint="var(--c-insurance)" label="Activity log"
          sub="Everything added, edited or deleted" onClick={() => go('activity')} />
        <SettingsRow icon="💾" tint="var(--c-investments)" label="Take my data to another computer"
          sub="Your own copy, for a USB drive" onClick={() => setExportScope('mine')} />
      </SettingsGroup>

      {user?.can_admin && (
        <SettingsGroup title="Administration"
          footer="Only administrators see this section.">
          <SettingsRow icon="👥" tint="var(--c-loans)" label="User management"
            sub="Create users, set permissions" onClick={() => go('admin')} />
          <SettingsRow icon="🗄️" tint="var(--c-cards)" label="Move everything to another computer"
            sub="Every account and all their data" onClick={() => setExportScope('all')} />
          <SettingsRow icon="🎫" tint="var(--c-vault)" label="Licences"
            sub={`Give someone a licensed copy of ${brand.app_name}`} onClick={() => go('licences')} />
          <SettingsRow icon="🎨" tint="var(--c-investments)" label="App name and icon"
            sub={`Currently “${brand.app_name}”`} onClick={() => setBrandOpen(true)} />
        </SettingsGroup>
      )}
      {brandOpen && <BrandingSheet onClose={() => setBrandOpen(false)} />}
      {webOpen && <WebAddress onClose={() => setWebOpen(false)} />}

      <WebAddressSection onOpen={() => setWebOpen(true)} />
      <PhoneBackupSection />
      <WatchFolderSection />
      <HouseholdSection />
      <AlwaysOnSection onOpenWeb={() => setWebOpen(true)} />
      <LocalNetworkSection />

      <MyLicence />

      <StorageUse />

      <ThisComputer />

      <AppStorage />

      <SettingsGroup>
        <SettingsRow icon={<IcLogout className="ic" />} tint="var(--danger)" label="Sign out"
          danger onClick={logout} right={<span />} />
      </SettingsGroup>

      <p className="muted" style={{ textAlign: 'center', fontSize: 12, margin: '4px 0 8px' }}>
        {brand.app_name} 2.0 · build {BUILD_ID} · {isStandalone() ? 'installed app' : 'browser tab'}
      </p>
      {/* Name and year both read from live values: a renamed app must not keep
          asserting copyright under its old name, and a hardcoded year silently
          becomes wrong every January. */}
      <p className="muted" style={{ textAlign: 'center', fontSize: 11.5, margin: '0 0 14px' }}>
        © {new Date().getFullYear()} {brand.app_name}. All rights reserved.
      </p>

      {pwOpen && <ChangePassword onClose={() => setPwOpen(false)} />}
      {editOpen && <EditProfile onClose={() => setEditOpen(false)} />}
      {exportScope && <ExportBundle scope={exportScope} onClose={() => setExportScope(null)} />}
    </div>
  )
}

/** Avatar with initials fallback, used on the profile card and in the edit sheet. */
function Avatar({ size }: { size: number }) {
  const { user } = useAuth()
  return user?.avatar_url
    ? <img src={user.avatar_url} className="avatar-img" alt=""
        style={{ width: size, height: size }} />
    : <div className="avatar" style={{ width: size, height: size, fontSize: size * 0.34 }}>
        {user?.initials}
      </div>
}

function EditProfile({ onClose }: { onClose: () => void }) {
  const { user, refreshUser } = useAuth()
  const toast = useToast()
  const pick = useRef<HTMLInputElement>(null)
  const [name, setName] = useState(user?.name || '')
  const [busy, setBusy] = useState<'' | 'save' | 'photo' | 'remove'>('')

  const nameProblem = name.trim().length < 2 ? 'Enter at least 2 characters' : ''
  const nameChanged = name.trim() !== (user?.name || '')

  async function save() {
    // The photo uploads the moment it is picked, so changing only the photo
    // leaves nothing to send. The button used to be disabled in that case, which
    // read as the app ignoring the click: the person had just changed their
    // picture, pressed Save changes, and nothing happened at all. Closing is the
    // honest response — the work is already done.
    if (!nameChanged) { onClose(); return }
    setBusy('save')
    try {
      await api('/api/auth/profile', { method: 'PUT', body: { name: name.trim() } })
      await refreshUser()
      toast('Profile updated'); onClose()
    } catch (e) { toast(errorMessage(e, 'Could not save')) }
    finally { setBusy('') }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setBusy('photo')
    try {
      const fd = new FormData()
      fd.append('file', files[0])
      const res = await fetch('/api/auth/avatar', {
        method: 'POST', headers: { Authorization: `Bearer ${tokenStore.get()}` }, body: fd,
      })
      if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail || 'Upload failed')
      await refreshUser()
      toast('Photo updated')
    } catch (e) { toast(errorMessage(e, 'Could not upload that photo')) }
    finally { setBusy('') }
  }

  async function removePhoto() {
    setBusy('remove')
    try {
      await api('/api/auth/avatar', { method: 'DELETE' })
      await refreshUser()
      toast('Photo removed')
    } catch (e) { toast(errorMessage(e, 'Could not remove the photo')) }
    finally { setBusy('') }
  }

  return (
    <Sheet title="Edit profile" onClose={onClose}>
      <input ref={pick} type="file" accept="image/*" hidden
        onChange={(e) => { upload(e.target.files); e.currentTarget.value = '' }} />

      <div className="pf-photo">
        <button className="pf-photo-btn" onClick={() => pick.current?.click()} disabled={!!busy}>
          <Avatar size={92} />
          <span className="pf-photo-edit">{busy === 'photo' ? '…' : '📷'}</span>
        </button>
        <div className="pf-photo-acts">
          <button className="btn ghost sm" onClick={() => pick.current?.click()} disabled={!!busy}>
            {user?.avatar_url ? 'Change photo' : 'Upload photo'}
          </button>
          {user?.avatar_url && (
            <button className="btn ghost sm" onClick={removePhoto} disabled={!!busy}>
              {busy === 'remove' ? '…' : 'Remove'}
            </button>
          )}
        </div>
      </div>

      <Field label="Name">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Your full name" autoFocus maxLength={120} />
      </Field>

      <Field label="Email">
        <input className="input" value={user?.email || ''} disabled readOnly />
      </Field>
      <p className="form-hint">
        Your email is your sign-in address and can only be changed by an administrator.
      </p>

      {nameChanged && nameProblem && <p className="form-hint warn">{nameProblem}</p>}
      {/* Only blocked by the name rule when the name was actually edited —
          otherwise someone whose stored name is short could never close this. */}
      <button className="btn block" disabled={(nameChanged && !!nameProblem) || !!busy}
        onClick={save}>
        {busy === 'save' ? 'Saving…' : nameChanged ? 'Save changes' : 'Done'}
      </button>
    </Sheet>
  )
}

/** Daily digest of what's due — opt-in, per device. */
function NotificationSettings() {
  const toast = useToast()
  const [s, setS] = useState<PushSettings | null>(null)
  const [busy, setBusy] = useState<'' | 'toggle' | 'test' | 'save' | 'fix'>('')
  const blocked = blockedReason()
  // What THIS phone is really registered for. The server-side count says how many
  // devices exist, not whether this one is among them — the difference is exactly
  // where "it says on but nothing arrives" comes from.
  const [device, setDevice] = useState<DeviceState | null>(null)

  useEffect(() => { getSettings().then(setS).catch(() => setS(null)) }, [])

  const checkDevice = useCallback(async (key: string | null) => {
    setDevice(await syncSubscription(key))
  }, [])
  useEffect(() => { if (s?.enabled) checkDevice(s.publicKey) }, [s?.enabled, s?.publicKey, checkDevice])

  async function repair() {
    if (!s) return
    setBusy('fix')
    try {
      // force: tear down whatever subscription exists and build a fresh one.
      const next = await syncSubscription(s.publicKey, { force: true })
      setDevice(next)
      setS(await getSettings())
      // Show the actual reason, not a generic failure — the reason is the whole
      // point of the button.
      toast(next.subscribed ? 'This device is registered — send a test to confirm'
                            : next.problem || 'Could not register this device')
    } finally { setBusy('') }
  }

  async function toggle() {
    if (!s) return
    setBusy('toggle')
    try {
      setS(s.enabled || s.devices > 0
        ? await disablePush()
        : await enablePush(s.publicKey || ''))
      toast(s.enabled ? 'Notifications turned off' : 'Notifications on for this device')
    } catch (e) { toast(errorMessage(e, 'Could not change notifications')) }
    finally { setBusy('') }
  }

  async function patch(next: Partial<PushSettings>) {
    setBusy('save')
    try { setS(await saveSettings(next)) }
    catch (e) { toast(errorMessage(e, 'Could not save')) }
    finally { setBusy('') }
  }

  async function test() {
    setBusy('test')
    try {
      const r = await sendTest()
      toast(r.sent ? `Sent to ${r.sent} device${r.sent === 1 ? '' : 's'}` : 'Could not deliver')
    } catch (e) { toast(errorMessage(e, 'Could not send a test')) }
    finally { setBusy('') }
  }

  if (!s) return null
  if (!s.available) {
    return (
      <SettingsGroup title="Notifications">
        <SettingsRow icon="🔕" tint="var(--ink-faint)" label="Not available"
          sub="Push notifications aren’t configured on the server." />
      </SettingsGroup>
    )
  }

  const on = s.enabled && s.devices > 0
  // <input type="time"> only accepts 24-hour HH:MM as its value; the label people
  // read is the 12-hour one.
  const time = `${String(s.sendHour).padStart(2, '0')}:${String(s.sendMinute).padStart(2, '0')}`
  const timeLabel = fmtTime(`2000-01-01T${time}:00`)

  return (
    <SettingsGroup title="Notifications"
      footer={on && !blocked
        ? `Sent from your own server, so it only goes out while the ${appName()} PC is on.`
        : undefined}>
      <SettingsRow icon="🔔" tint="var(--c-reminders)" label="Daily reminder"
        sub={on ? `One summary each day at ${timeLabel} IST` : 'Get told what’s due, once a day'}
        right={
          <button className={`switch${on ? ' on' : ''}`} onClick={toggle}
            disabled={!!busy || !!blocked} role="switch" aria-checked={on} aria-label="Daily reminder">
            <span />
          </button>
        } />

      {blocked && <SettingsBlock><p className="form-hint warn" style={{ margin: 0 }}>{blocked}</p></SettingsBlock>}

      {on && !blocked && device && (
        device.subscribed
          ? <SettingsRow icon="✓" tint="var(--ok)" label="This device is registered"
              sub={`${s.devices} device${s.devices === 1 ? '' : 's'} will receive alerts`} />
          : <SettingsRow icon="!" tint="var(--danger)" label="This device is NOT registered"
              sub={device.problem || 'Alerts are being sent but cannot reach this phone.'}
              right={<button className="btn ghost sm" disabled={!!busy} onClick={repair}>
                {busy === 'fix' ? 'Fixing…' : 'Fix'}</button>} />
      )}

      {on && !blocked && (
        <>
          <SettingsRow icon="🕗" tint="var(--c-todo)" label="Send at" right={
            <input className="input notif-time" type="time" value={time}
              onChange={(e) => {
                const [h, m] = e.target.value.split(':').map(Number)
                if (!Number.isNaN(h)) patch({ sendHour: h, sendMinute: m || 0 })
              }} />
          } />

          <SettingsBlock>
            <div className="notif-checks">
              {([
                ['includeBills', 'Card bills & loan EMIs'],
                ['includeReminders', 'Reminders & tasks'],
                ['includeExpiry', 'Policy renewals & document expiry'],
              ] as const).map(([key, label]) => (
                <button key={key} className={`notif-check${s[key] ? ' on' : ''}`}
                  disabled={!!busy} onClick={() => patch({ [key]: !s[key] })}>
                  <span className="tick">{s[key] ? '✓' : ''}</span>{label}
                </button>
              ))}
            </div>
          </SettingsBlock>

          <SettingsRow icon="✉️" tint="var(--c-insurance)"
            label={busy === 'test' ? 'Sending…' : 'Send a test notification'}
            sub={s.devices > 1 ? `${s.devices} devices enrolled` : undefined}
            onClick={busy ? undefined : test} />
        </>
      )}
    </SettingsGroup>
  )
}

interface SysStatus {
  version: string
  cdnPurge: { available: boolean; isAdmin: boolean; targets: number }
}

/* ---------- Move to another computer ---------- */

type ExportPlatform = 'windows' | 'mac'
type ExportScope = 'mine' | 'all'

interface ExportJob {
  state: 'idle' | 'running' | 'done' | 'error' | 'busy'
  step: string
  percent: number
  error: string | null
  default_path: string
  by?: string | null
  scope?: ExportScope | null
  result: {
    folder: string
    bytes: number
    with_data: boolean
    database: boolean
    media_files: number
    platform: ExportPlatform
    scope: ExportScope
    rows?: number
    unreadable_vault_fields?: number
    zip?: string
    zip_bytes?: number
    /** The exact launcher filename the build produced. It carries the app's
     *  current name, so it must come from the server rather than be guessed
     *  here — a guess would name a file the customer does not have. */
    launcher?: string
    app_name?: string
  } | null
}

interface ExportLogRow {
  id: number
  at: string | null
  by: string
  mine: boolean
  platform: ExportPlatform | null
  scope: ExportScope
  state: string
  error: string | null
  with_data: boolean
  path: string | null
  name: string | null
  exists: boolean
  bytes: number | null
  media_files: number | null
}

/** Last path segment, for either separator — the server may be Windows or Unix. */
const baseName = (p?: string | null) => (p || '').split(/[\\/]/).pop() || (p || '')

/**
 * Copies text to the clipboard. The modern API needs a secure context, which a
 * plain http:// LAN address is not — so there's a fallback, and if both fail the
 * text is selected instead so a long-press can copy it by hand.
 */
function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const toast = useToast()
  const [copied, setCopied] = useState(false)

  async function copy() {
    let done = false
    try {
      await navigator.clipboard.writeText(value)
      done = true
    } catch {
      const ta = document.createElement('textarea')
      ta.value = value
      ta.setAttribute('readonly', '')
      ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0'
      document.body.appendChild(ta)
      ta.select()
      try { done = document.execCommand('copy') } catch { done = false }
      ta.remove()
    }
    if (done) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } else {
      toast('Could not copy — press and hold the path to select it')
    }
  }

  return (
    <button className={`btn ghost sm exp-copy${copied ? ' ok' : ''}`} onClick={copy}>
      {copied ? '✓ Copied' : `⧉ ${label}`}
    </button>
  )
}

/** Past exports, so you can find a folder you made earlier without redoing it. */
function ExportLog({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<ExportLogRow[] | null>(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    api<{ items: ExportLogRow[] }>('/api/system/export/history')
      .then((d) => setRows(d.items)).catch(() => setRows([]))
  }, [refreshKey])

  if (!rows || rows.length === 0) return null
  const shown = open ? rows : rows.slice(0, 3)

  return (
    <div style={{ marginTop: 18 }}>
      <div className="section-title" style={{ marginTop: 0 }}>Previous exports</div>
      <div className="list">
        {shown.map((r) => (
          <div key={r.id} className="card exp-log">
            <div className="exp-log-top">
              <span className="exp-log-ic">{r.platform === 'mac' ? '🍎' : '🪟'}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="exp-log-name">{r.name || (r.state === 'error' ? 'Failed' : '—')}</div>
                <div className="exp-log-sub">
                  {fmtDateTime(r.at)}
                  {!r.mine && ` · ${r.by}`}
                  {r.scope === 'mine' ? ' · own data' : ' · everything'}
                </div>
              </div>
              {r.state === 'error'
                ? <span className="pill danger">Failed</span>
                : !r.exists
                  ? <span className="pill muted" style={{ whiteSpace: 'nowrap' }}>Removed</span>
                  : <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                      {r.bytes ? formatBytes(r.bytes) : ''}
                    </span>}
            </div>
            {r.state === 'error' && r.error && (
              <div className="exp-log-err">{r.error}</div>
            )}
            {r.path && r.exists && (
              <>
                <div className="exp-path">{r.path}</div>
                <CopyButton value={r.path} label="Copy path" />
              </>
            )}
            {r.path && !r.exists && r.state !== 'error' && (
              <div className="exp-log-gone">
                No longer on this computer — it was moved or deleted. Create a new copy
                if you need it again.
              </div>
            )}
          </div>
        ))}
      </div>
      {rows.length > 3 && (
        <button className="btn ghost sm block" style={{ marginTop: 10 }}
          onClick={() => setOpen((o) => !o)}>
          {open ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}
      <p className="muted" style={{ fontSize: 11.5, marginTop: 10, lineHeight: 1.5 }}>
        These folders stay on the {appName()} computer until you delete them. Each one holds
        real data — remove the ones you no longer need.
      </p>
    </div>
  )
}

/**
 * Builds a USB-ready copy of the app on the machine that runs it. The work happens
 * on the server (thousands of files, minutes long), so this starts the job and then
 * polls — closing the sheet, or the phone sleeping, doesn't interrupt it.
 */
function ExportBundle({ scope, onClose }: { scope: ExportScope; onClose: () => void }) {
  const toast = useToast()
  const [platform, setPlatform] = useState<ExportPlatform | ''>('')
  const [withData, setWithData] = useState(true)
  const [job, setJob] = useState<ExportJob | null>(null)
  const [starting, setStarting] = useState(false)
  // Whether this device can actually receive a "your copy is ready" push. Offering
  // the choice without checking would promise an alert that never arrives.
  const [push, setPush] = useState<PushSettings | null>(null)
  const [notify, setNotify] = useState(true)
  useEffect(() => { getSettings().then(setPush).catch(() => setPush(null)) }, [])
  const canNotify = !!push?.available && !!push?.enabled && (push?.devices ?? 0) > 0

  const mine = scope === 'mine'
  const running = job?.state === 'running'

  // Which screen the sheet is on. The job state lives on the SERVER and outlives
  // the sheet, so without this the completion screen came back every time you
  // reopened — showing a folder you may since have deleted, with no route back to
  // the form. Opening the sheet always starts on the form; the result is only
  // shown for a build watched from here, or one already running on arrival.
  const [phase, setPhase] = useState<'form' | 'watch'>('form')

  // Bumped when a job finishes, so the history below reloads with the new entry.
  const [logKey, setLogKey] = useState(0)

  // Poll while a build is in flight. Picks up a job already running from another
  // device, too — the state lives on the server, not in this sheet.
  useEffect(() => {
    let alive = true
    let wasRunning = false
    let first = true
    const tick = () => api<ExportJob>('/api/system/export')
      .then((j) => {
        if (!alive) return
        setJob(j)
        // A build already under way when the sheet opens is worth showing.
        if (first && (j.state === 'running' || j.state === 'busy')) setPhase('watch')
        first = false
        if (wasRunning && j.state !== 'running') setLogKey((k) => k + 1)
        wasRunning = j.state === 'running'
      })
      .catch(() => {})
    tick()
    const id = window.setInterval(() => { if (alive) tick() }, 2000)
    return () => { alive = false; window.clearInterval(id) }
  }, [])

  async function start() {
    if (!platform) return
    setStarting(true)
    try {
      const j = await api<ExportJob>('/api/system/export', {
        method: 'POST',
        body: { platform, include_data: withData, scope, notify: notify && canNotify },
      })
      setJob(j)
      setPhase('watch')
    } catch (e) { toast(errorMessage(e, 'Could not start the export')) }
    finally { setStarting(false) }
  }

  const done = phase === 'watch' && job?.state === 'done' && job.result
  const built = job?.result
  // A Mac bundle is delivered as a zip: it's the only container that carries the
  // launcher's executable bit across Windows and a USB stick, so Finder will run it.
  const isMac = built?.platform === 'mac'
  const carry = isMac && built?.zip ? built.zip : built?.folder
  const carrySize = isMac && built?.zip_bytes ? built.zip_bytes : built?.bytes
  const launcher = built?.launcher
    || (isMac ? `Start ${appName()} (Mac).command` : `Start ${appName()} (Windows).bat`)

  // Another person's export is reported as "busy" with no detail — their folder
  // path and row counts aren't ours to display.
  if (phase === 'watch' && job?.state === 'busy') {
    return (
      <Sheet title={mine ? 'Take my data' : 'Move everything'} onClose={onClose}>
        <div style={{ textAlign: 'center', fontSize: 36, marginBottom: 8 }}>⏳</div>
        <p style={{ fontSize: 14, textAlign: 'center' }}>
          {job.by ? `${job.by} is` : 'Someone else is'} exporting right now.
        </p>
        <p className="muted" style={{ fontSize: 13, textAlign: 'center', marginTop: 8 }}>
          Only one export can run at a time. Try again in a few minutes.
        </p>
        <button className="btn ghost block" style={{ marginTop: 16 }} onClick={onClose}>Close</button>
      </Sheet>
    )
  }

  return (
    <Sheet title={mine ? 'Take my data to another computer' : 'Move everything'} onClose={onClose}>
      {done ? (
        <>
          <div style={{ textAlign: 'center', fontSize: 40, marginBottom: 6 }}>✅</div>
          <p style={{ fontSize: 14, textAlign: 'center', marginBottom: 14 }}>
            Your copy is ready on the {appName()} computer.
          </p>
          <div className="card" style={{ padding: 12, marginBottom: 14 }}>
            <div className="muted" style={{ fontSize: 12 }}>Name</div>
            <div style={{ fontWeight: 800, fontSize: 14, overflowWrap: 'anywhere' }}>
              {baseName(carry)}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
              Full path on the {appName()} computer
            </div>
            <div className="exp-path">{carry}</div>
            <CopyButton value={carry || ''} label="Copy full path" />
            <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
              {formatBytes(carrySize || 0)}
              {built!.with_data
                ? ` · ${built!.media_files.toLocaleString()} photos & documents`
                : ' · no data included'}
            </div>
          </div>
          <ol className="exp-steps">
            <li>On the {appName()} computer, copy that {isMac ? 'zip file' : 'folder'} onto
                a USB drive.</li>
            <li>Plug the drive into the other computer and copy it across.</li>
            {isMac && <li>Double-click the zip to unpack it.</li>}
            <li>Open the folder and double-click <b>{launcher}</b>.
                {isMac && <> If macOS says it's from an unidentified developer,
                right-click it → <b>Open</b> → <b>Open</b>.</>}</li>
            <li>It asks a few questions — including your web address if you want to
                reach it from outside the house — then starts on its own.</li>
          </ol>
          {isMac && (
            <p className="form-hint" style={{ marginTop: 12 }}>
              Use the zip rather than the folder. macOS needs the launcher marked as
              runnable, and only the zip carries that across from Windows.
            </p>
          )}
          {built!.with_data && (
            <p className="form-hint warn" style={{ marginTop: 12 }}>
              This holds {built!.scope === 'mine' ? 'your files' : "every account's files"} and
              the key the vault is encrypted with. Move it on a USB drive, not by email or
              a shared cloud folder, and delete the copy from the drive once it's in place.
            </p>
          )}
          {!!built!.unreadable_vault_fields && (
            <p className="form-hint" style={{ marginTop: 10 }}>
              {built!.unreadable_vault_fields} vault entr
              {built!.unreadable_vault_fields === 1 ? 'y' : 'ies'} could not be re-locked
              because {built!.unreadable_vault_fields === 1 ? 'it is' : 'they are'} already
              unreadable on this server. Everything else came across.
            </p>
          )}
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            <button className="btn ghost block" onClick={() => { setPlatform(''); setPhase('form') }}>
              Create another
            </button>
            <button className="btn block" onClick={onClose}>Done</button>
          </div>
          <ExportLog refreshKey={logKey} />
        </>
      ) : (phase === 'watch' && running) ? (
        <>
          <p style={{ fontSize: 14, marginBottom: 12 }}>{job!.step || 'Working…'}</p>
          <div className="exp-bar"><span style={{ width: `${Math.max(3, job!.percent)}%` }} /></div>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
            {job!.percent}% · this takes a few minutes with a large gallery. It runs on the
            {appName()} computer, so you can close this — or the whole app — and it keeps going.
          </p>
          <p className="form-hint" style={{ marginTop: 10 }}>
            {notify && canNotify
              ? '🔔 You’ll get a notification when it’s ready.'
              : 'No notification was requested — reopen this screen to check on it.'}
          </p>
          <button className="btn ghost block" style={{ marginTop: 14 }} onClick={onClose}>
            Close and let it run
          </button>
        </>
      ) : (
        <>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 14 }}>
            {mine
              ? `Makes a complete copy of ${appName()} containing only your own data, ready to run on your own computer.`
              : `Makes a complete copy of ${appName()} — every account and all their data — ready to run on another computer.`}
          </p>
          {mine && (
            <div className="card" style={{ padding: 12, marginBottom: 14 }}>
              <div style={{ fontWeight: 700, fontSize: 13.5, marginBottom: 6 }}>What comes with you</div>
              <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                Your photos, documents, expenses, loans, cards, policies, investments,
                reminders, tasks and vault — and nothing belonging to anyone else.
                Your vault is re-locked with a new key made just for your copy, and you
                sign in with the same email and password.
              </div>
            </div>
          )}

          <div className="section-title" style={{ marginTop: 0 }}>Which computer is it for?</div>
          <div className="exp-picker">
            {(['windows', 'mac'] as ExportPlatform[]).map((p) => (
              <button key={p} className={`exp-choice${platform === p ? ' on' : ''}`}
                onClick={() => setPlatform(p)}>
                <span className="exp-ic">{p === 'windows' ? '🪟' : '🍎'}</span>
                <span>{p === 'windows' ? 'Windows' : 'Mac'}</span>
              </button>
            ))}
          </div>

          <label className="exp-toggle">
            <input type="checkbox" checked={withData} onChange={(e) => setWithData(e.target.checked)} />
            <span>
              <b>{mine ? 'Include my data' : "Include everyone's data"}</b>
              <span className="muted" style={{ display: 'block', fontSize: 12 }}>
                Photos, documents, records and vault. Turn off for a clean, empty copy.
              </span>
            </span>
          </label>

          <label className={`exp-toggle${canNotify ? '' : ' off'}`} style={{ marginTop: 10 }}>
            <input type="checkbox" checked={notify && canNotify} disabled={!canNotify}
              onChange={(e) => setNotify(e.target.checked)} />
            <span>
              <b>Tell me when it's ready</b>
              <span className="muted" style={{ display: 'block', fontSize: 12 }}>
                {canNotify
                  ? 'Sends a notification to your phone so you don’t have to keep this open.'
                  : 'Turn on Notifications above to use this. Without it, reopen this screen to check.'}
              </span>
            </span>
          </label>

          {job?.state === 'error' && (
            <p className="form-hint err" style={{ marginTop: 12 }}>{job.error}</p>
          )}

          <button className="btn block" style={{ marginTop: 14 }}
            disabled={!platform || starting} onClick={start}>
            {starting ? 'Starting…' : platform ? 'Create the copy' : 'Choose Windows or Mac'}
          </button>
          <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            It will be saved on the {appName()} computer at<br />
            <b style={{ overflowWrap: 'anywhere' }}>{job?.default_path || '…'}</b>
          </p>
          <ExportLog refreshKey={logKey} />
        </>
      )}
    </Sheet>
  )
}

/** Self-service fix for the commonest PWA failure: a stale cached build. */
function AppStorage() {
  const toast = useToast()
  const [info, setInfo] = useState<StorageInfo | null>(null)
  const [sys, setSys] = useState<SysStatus | null>(null)
  const [busy, setBusy] = useState<'' | 'check' | 'clear' | 'purge'>('')
  const [confirmClear, setConfirmClear] = useState(false)

  const refresh = useCallback(() => { storageInfo().then(setInfo) }, [])
  useEffect(() => {
    refresh()
    api<SysStatus>('/api/system/status').then(setSys).catch(() => setSys(null))
  }, [refresh])

  async function update() {
    setBusy('check')
    try {
      const found = await checkForUpdate()
      if (found) { toast('New version found · reloading'); return } // reload is imminent
      toast("You're on the latest version")
    } catch { toast('Could not check for updates') }
    finally { setBusy('') }
  }

  async function clear() {
    setBusy('clear')
    try {
      await clearAppCache()
      toast('Cache cleared · reloading')
      setTimeout(() => location.reload(), 600)
    } catch { toast('Could not clear the cache'); setBusy('') }
  }

  async function purge() {
    setBusy('purge')
    try {
      const r = await api<{ count: number }>('/api/system/purge-cdn', { method: 'POST' })
      toast(`Purged ${r.count} file${r.count === 1 ? '' : 's'} from the CDN`)
    } catch (e) { toast(e instanceof ApiError ? e.message : 'Purge failed') }
    finally { setBusy('') }
  }

  const showPurge = sys?.cdnPurge.isAdmin && sys.cdnPurge.available

  return (
    <>
      {/* Titled for the phone, not the server — "Storage used" above is the
          the app computer, and two sections called storage would read as the
          same number measured twice. */}
      <SettingsGroup title="App &amp; updates"
        footer="If the app looks out of date or a new feature is missing, clear the cached data — it reloads the latest version without signing you out.">
        <SettingsRow icon="●" tint="var(--ink-faint)" label="Build" value={BUILD_ID} />
        <SettingsRow icon="▤" tint="var(--ink-faint)" label="Cached on this phone"
          value={info ? (info.usedBytes === null ? 'Not reported by this browser' : formatBytes(info.usedBytes)) : '…'} />
        <SettingsRow icon="☁" tint={info?.hasWorker ? 'var(--ok)' : 'var(--ink-faint)'}
          label="Offline mode" value={info?.hasWorker ? 'Active' : 'Off'} />
        <SettingsRow icon="⟳" tint="var(--c-insurance)"
          label={busy === 'check' ? 'Checking…' : 'Refresh the web app'}
          sub="Reloads this page's files; does not change the installed program"
          onClick={busy ? undefined : update} />
        <AppUpdateRow />
        <SettingsRow icon="🧹" tint="var(--warn)" label="Clear cached data"
          onClick={busy ? undefined : () => setConfirmClear(true)} />
        {showPurge && (
          <SettingsRow icon="☁" tint="var(--c-documents)"
            label={busy === 'purge' ? 'Purging…' : 'Purge CDN cache'}
            sub="Force every device to fetch the newest build"
            onClick={busy ? undefined : purge} />
        )}
      </SettingsGroup>

      {confirmClear && (
        <Sheet title="Clear cached data?" onClose={() => setConfirmClear(false)}>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 14 }}>
            Removes the offline copy of the app and its saved pages, then reloads the newest
            version. You’ll stay signed in and <b>nothing on the server is deleted</b>.
          </p>
          <p className="muted" style={{ fontSize: 12.5, marginBottom: 16 }}>
            Any photo uploads still waiting in the queue will be cleared — re-pick those photos
            afterwards. Offline access rebuilds itself as you use the app.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirmClear(false)}>Cancel</button>
            <button className="btn danger block" disabled={busy === 'clear'} onClick={clear}>
              {busy === 'clear' ? 'Clearing…' : 'Clear & reload'}
            </button>
          </div>
        </Sheet>
      )}
    </>
  )
}

function ChangePassword({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const MIN_LEN = 12 // must match backend security.MIN_PASSWORD_LEN
  const mismatch = confirm.length > 0 && next !== confirm
  const valid = cur && next.length >= MIN_LEN && next === confirm

  async function submit() {
    setErr(''); setBusy(true)
    try {
      // Changing the password revokes every existing token, so adopt the fresh one
      // the server hands back — otherwise this tab logs itself out.
      const r = await api<{ token?: string }>('/api/auth/change-password', {
        method: 'POST', body: { current_password: cur, new_password: next },
      })
      if (r?.token) tokenStore.set(r.token)
      toast('Password changed · other devices signed out'); onClose()
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Could not change password')
    } finally { setBusy(false) }
  }

  return (
    <Sheet title="Change password" onClose={onClose}>
      <Field label="Current password">
        <input className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} placeholder="••••••••" />
      </Field>
      <Field label="New password">
        <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} placeholder={`At least ${MIN_LEN} characters`} />
      </Field>
      <Field label="Confirm new password">
        <input className="input" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Re-enter new password" />
      </Field>
      {next.length > 0 && next.length < MIN_LEN && <p className="pill warn" style={{ width: '100%', justifyContent: 'center', padding: 9, marginBottom: 10 }}>New password needs at least {MIN_LEN} characters</p>}
      {mismatch && <p className="pill danger" style={{ width: '100%', justifyContent: 'center', padding: 9, marginBottom: 10 }}>Passwords don’t match</p>}
      {err && <p className="pill danger" style={{ width: '100%', justifyContent: 'center', padding: 10, marginBottom: 10 }}>{err}</p>}
      <button className="btn block" disabled={!valid || busy} onClick={submit}>{busy ? 'Saving…' : 'Update password'}</button>
    </Sheet>
  )
}

// ---------------------------------------------------------------- this computer
const PLATFORM: Record<string, { icon: string; name: string; tint: string }> = {
  windows: { icon: '🪟', name: 'Windows', tint: 'var(--c-expenses)' },
  mac: { icon: '🍎', name: 'Mac', tint: 'var(--ink-faint)' },
  linux: { icon: '🐧', name: 'Linux', tint: 'var(--c-reminders)' },
}

function hostLabel(h: AppHost) {
  return `${PLATFORM[h.platform]?.name ?? h.platform} · ${h.hostname}`
}

/** Which machine is answering, and every machine that has answered before.
 *
 *  the app travels — to a Mac, to an external drive, to a replacement laptop —
 *  and from a phone all of those look the same. When two copies end up serving
 *  the same address, records seem to appear and vanish at random, and this is
 *  the only place that makes the cause visible. */
function ThisComputer() {
  const [report, setReport] = useState<HostReport | null>(null)
  const [openHistory, setOpenHistory] = useState(false)

  useEffect(() => {
    api<HostReport>('/api/system/host').then(setReport).catch(() => { })
  }, [])

  if (!report) return null
  const now = report.current
  const look = PLATFORM[now.platform] ?? { icon: '💻', name: now.platform, tint: 'var(--ink-faint)' }
  const others = report.history.filter(h => !h.is_current)

  return (
    <>
      <SettingsGroup title="This computer"
        footer={others.length > 0
          ? `${appName()} has run on more than one computer. Only one should serve your web address at a time — two will answer from two different sets of records.`
          : `Where ${appName()} is running right now.`}>
        <SettingsRow icon={look.icon} tint={look.tint} label={hostLabel(now)} sub={now.os_name} />
        <SettingsRow icon="📶" tint="var(--c-gallery)" label="On your Wi-Fi"
          value={now.local_ip || 'unknown'} />
        {now.public_url && (
          <SettingsRow icon="🌐" tint="var(--brand)" label="Web address"
            sub={now.public_url.replace(/^https?:\/\//, '')} />
        )}
        <RecordsLocationRow fallback={now.data_dir} />
        {now.first_seen && (
          <SettingsRow icon="🕑" tint="var(--ink-faint)" label="Serving from here since"
            sub={now.first_seen} />
        )}
        {others.length > 0 && (
          <SettingsRow icon="📍" tint="var(--c-loans)" label={`Where ${appName()} has run`}
            sub={`${report.moves} previous computer${report.moves === 1 ? '' : 's'}`}
            onClick={() => setOpenHistory(true)} />
        )}
      </SettingsGroup>

      {openHistory && (
        <Sheet title={`Where ${appName()} has run`} onClose={() => setOpenHistory(false)}>
          <p className="muted" style={{ fontSize: 13, marginBottom: 14 }}>
            Every computer this copy of {appName()} has started on, most recent first.
          </p>
          <div className="host-list">
            {report.history.map(h => (
              <div key={h.id ?? h.hostname} className={`host-item${h.is_current ? ' now' : ''}`}>
                <span className="host-ic">{PLATFORM[h.platform]?.icon ?? '💻'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="host-name">
                    {hostLabel(h)}
                    {h.is_current && <span className="host-tag">running now</span>}
                  </div>
                  <div className="host-meta">{h.os_name}</div>
                  <div className="host-meta">
                    {h.local_ip && <>Wi-Fi address {h.local_ip}<br /></>}
                    {h.data_dir && <>Records in {h.data_dir}<br /></>}
                    First seen {h.first_seen ?? '—'}
                    {!h.is_current && h.last_seen && <> · last seen {h.last_seen}</>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Sheet>
      )}
    </>
  )
}

// -------------------------------------------------------------------- storage
const SLICES = [
  { key: 'gallery', label: 'Photos', icon: '🖼️', tint: 'var(--c-gallery)' },
  { key: 'documents', label: 'Documents', icon: '📄', tint: 'var(--c-documents)' },
  { key: 'avatars', label: 'Profile photo', icon: '👤', tint: 'var(--c-loans)' },
] as const

/** How much room the app is taking on the computer that runs it.
 *
 *  Measured from the disk on every request rather than from stored sizes, so
 *  uploading ten photos today and thirty tomorrow needs nothing to be
 *  recalculated or kept in step — the number is whatever is actually there.
 *  Re-fetched whenever the screen is opened or the app comes back to the
 *  foreground, so it can't sit showing yesterday's figure. */
function StorageUse() {
  const [r, setR] = useState<StorageReport | null>(null)

  const load = useCallback(() => {
    api<StorageReport>('/api/system/storage').then(setR).catch(() => { })
  }, [])

  useEffect(() => {
    load()
    // Coming back from the Gallery after an upload must not show a stale total.
    const onWake = () => { if (document.visibilityState === 'visible') load() }
    document.addEventListener('visibilitychange', onWake)
    window.addEventListener('focus', load)
    return () => {
      document.removeEventListener('visibilitychange', onWake)
      window.removeEventListener('focus', load)
    }
  }, [load])

  if (!r) return null
  const admin = r.server != null
  const files = admin ? r.server! : r.mine
  const dbBytes = r.database ?? 0
  const total = files.bytes + dbBytes

  // Proportional bar. Segments under a pixel still get one, so a small category
  // reads as "present but tiny" rather than vanishing.
  const parts = [
    ...SLICES.map(s => ({ ...s, bytes: files.modules[s.key].bytes })),
    ...(admin ? [{ key: 'db', label: 'Records', icon: '🗄️', tint: 'var(--c-investments)', bytes: dbBytes }] : []),
  ].filter(p => p.bytes > 0)

  return (
    <SettingsGroup title="Storage used"
      footer={admin
        ? 'Everything stored by every account on this computer. Counted from the files themselves each time you open this screen.'
        : 'Everything you have stored. Counted each time you open this screen, so it is always current.'}>
      <SettingsBlock>
        <div className="stg-total">{formatBytes(total)}</div>
        <div className="stg-sub">
          {files.files.toLocaleString()} file{files.files === 1 ? '' : 's'}
          {admin && dbBytes > 0 && <> · plus {formatBytes(dbBytes)} of records</>}
        </div>
        {total > 0 && (
          <div className="stg-bar">
            {parts.map(p => (
              <span key={p.key} title={`${p.label} — ${formatBytes(p.bytes)}`}
                style={{ width: `${Math.max(1.5, (p.bytes / total) * 100)}%`, background: p.tint }} />
            ))}
          </div>
        )}
      </SettingsBlock>

      {SLICES.map(s => (
        <SettingsRow key={s.key} icon={s.icon} tint={s.tint} label={s.label}
          sub={`${files.modules[s.key].files.toLocaleString()} file${files.modules[s.key].files === 1 ? '' : 's'}`}
          value={formatBytes(files.modules[s.key].bytes)} />
      ))}

      {admin && (
        <SettingsRow icon="🗄️" tint="var(--c-investments)" label="Records"
          sub="Expenses, reminders, activity log and the rest"
          value={formatBytes(dbBytes)} />
      )}

      {r.disk && (
        <SettingsRow icon="💽" tint={r.disk.free < 5e9 ? 'var(--warn)' : 'var(--ok)'}
          label="Free on this computer"
          sub={`of ${formatBytes(r.disk.total)} total`}
          value={formatBytes(r.disk.free)} />
      )}
    </SettingsGroup>
  )
}

// -------------------------------------------------------------------- licence
/** This copy's own licence, shown only when it is running under one.
 *
 *  The publisher's own installation is unlicensed and renders nothing here. A
 *  customer's copy shows the expiry every time they visit Profile, because the
 *  worst version of this feature is one where the app simply stops one morning
 *  with no warning anyone could have acted on. */
function MyLicence() {
  const [lic, setLic] = useState<LicenceStatus | null>(null)

  useEffect(() => {
    api<LicenceStatus>('/api/licence/status').then(setLic).catch(() => { })
  }, [])

  if (!lic?.licensed) return null
  const warn = lic.state === 'expiring' || lic.state === 'grace'
  const bad = lic.state === 'expired' || lic.state === 'revoked' || lic.state === 'invalid'
  const tint = bad ? 'var(--danger)' : warn ? 'var(--warn)' : 'var(--ok)'

  return (
    <SettingsGroup title="Licence"
      footer={lic.reason || `This copy of ${appName()} is licensed. It keeps working until the date shown.`}>
      <SettingsRow icon="🎫" tint={tint} label={lic.name || 'Licensed copy'}
        sub={lic.email} />
      <SettingsRow icon="📅" tint="var(--ink-faint)" label="Valid until"
        value={lic.expires_on || '—'} />
      {lic.days_left != null && lic.days_left >= 0 && (
        <SettingsRow icon="⏳" tint={tint} label="Days remaining"
          value={String(lic.days_left)} />
      )}
      <SettingsRow icon="#" tint="var(--ink-faint)" label="Licence number"
        value={lic.key_id || '—'} />
      {/* The machine details this copy sends when it validates its licence used to
          be listed here, under "What this copy reports". It was meant as candour
          and landed as a warning: customers read "sent to <the supplier> once a
          day" as the app phoning home about them, on their own computer. The facts
          belong in the licence terms, not as an alarming line in Settings. */}
    </SettingsGroup>
  )
}

/** Set what this app is called and what it looks like.
 *
 *  Admin only. The name reaches the browser tab, the login screen, the home-screen
 *  shortcut and every place the interface refers to the app by name; the icon is
 *  rendered server-side into each size the platforms want, so one upload covers
 *  the favicon, Android and iOS rather than looking right on only one of them.
 */
function BrandingSheet({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const live = useBranding()
  const pick = useRef<HTMLInputElement>(null)
  const [name, setName] = useState(live.app_name)
  const [short, setShort] = useState(live.short_name)
  const [tagline, setTagline] = useState(live.tagline)
  const [busy, setBusy] = useState<'' | 'save' | 'icon' | 'clear'>('')

  const problem = name.trim().length < 2 ? 'Give the app a name of at least 2 characters'
    : short.trim().length < 2 ? 'The short name is used on phone home screens — at least 2 characters'
      : ''

  async function save() {
    setBusy('save')
    try {
      const b = await api<Branding>('/api/branding', {
        method: 'PUT',
        body: { app_name: name.trim(), short_name: short.trim(), tagline: tagline.trim(),
                theme_color: live.theme_color },
      })
      brandStore.set(b)
      toast(`Now called ${b.app_name}`)
      onClose()
    } catch (e) { toast(errorMessage(e, 'Could not save that')) }
    finally { setBusy('') }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setBusy('icon')
    try {
      const fd = new FormData()
      fd.append('file', files[0])
      const res = await fetch('/api/branding/icon', {
        method: 'POST', headers: { Authorization: `Bearer ${tokenStore.get()}` }, body: fd,
      })
      if (!res.ok) {
        throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail || 'Upload failed')
      }
      brandStore.set(await res.json())
      toast('Icon updated everywhere')
    } catch (e) { toast(errorMessage(e, 'Could not use that image')) }
    finally { setBusy(''); if (pick.current) pick.current.value = '' }
  }

  async function clearIcon() {
    setBusy('clear')
    try {
      brandStore.set(await api<Branding>('/api/branding/icon', { method: 'DELETE' }))
      toast('Back to the original icon')
    } catch (e) { toast(errorMessage(e, 'Could not reset the icon')) }
    finally { setBusy('') }
  }

  return (
    <Sheet title="App name and icon" onClose={onClose}>
      <div className="brand-preview">
        <div className="brand-preview-ic">
          {live.icon_version > 0
            ? <img src={live.icons['192']} alt="" />
            : <span>₹</span>}
        </div>
        <div className="brand-preview-t">
          <b>{name.trim() || 'Your app'}</b>
          <span>{tagline.trim() || 'No tagline set'}</span>
        </div>
      </div>

      <input ref={pick} type="file" accept="image/*" hidden
        onChange={(e) => upload(e.target.files)} />
      <div className="brand-actions">
        <button className="btn" disabled={!!busy} onClick={() => pick.current?.click()}>
          {busy === 'icon' ? 'Uploading…' : live.icon_version > 0 ? 'Change icon' : 'Upload an icon'}
        </button>
        {live.icon_version > 0 && (
          <button className="btn ghost" disabled={!!busy} onClick={clearIcon}>
            {busy === 'clear' ? 'Resetting…' : 'Use the original'}
          </button>
        )}
      </div>
      <p className="form-hint">
        A square PNG works best. Anything else is padded to a square rather than
        cropped, so a wide logo keeps both its ends. One upload covers the browser
        tab, Android and iPhone.
      </p>

      <Field label="App name">
        <input className="input" value={name} maxLength={60}
          onChange={(e) => setName(e.target.value)} placeholder="SafeNest" />
      </Field>
      <Field label="Short name (phone home screen)">
        <input className="input" value={short} maxLength={20}
          onChange={(e) => setShort(e.target.value)} placeholder="SafeNest" />
      </Field>
      <Field label="Tagline (optional)">
        <input className="input" value={tagline} maxLength={120}
          onChange={(e) => setTagline(e.target.value)}
          placeholder="Everything you own, kept safe at home" />
      </Field>

      {problem && <p className="form-hint warn">{problem}</p>}
      <p className="form-hint">
        The name appears on the sign-in screen, the browser tab and throughout the
        app. An icon already added to a phone's home screen keeps its old name and
        picture until it is removed and added again — that is the phone's doing,
        not a fault here.
      </p>

      <button className="btn block" disabled={!!busy || !!problem} onClick={save}>
        {busy === 'save' ? 'Saving…' : 'Save'}
      </button>
    </Sheet>
  )
}

/* ===========================================================================
   Web address - a guided walkthrough, not a form.
   ===========================================================================
   Whoever does this may never have used Cloudflare. The first version assumed
   they already had a tunnel id to paste, which is the LAST step of a process
   nothing on screen explained. So this walks the whole way through, and builds
   every command with their own domain already in it - there is nothing left to
   work out and nothing to substitute by hand.
*/
interface HostingState {
  public_url: string
  hostname: string
  from_env: boolean
  env_url: string
  tunnel_hostname: string
  tunnel_id: string
  has_token: boolean
  token_hint: string
  config_path: string
  config_written: boolean
  updated_at: string | null
}

/** A command with a copy button. People mistype these; copying is the point. */
function Cmd({ text }: { text: string }) {
  const toast = useToast()
  const [done, setDone] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setDone(true); setTimeout(() => setDone(false), 1600)
    } catch { toast('Could not copy - select the text and copy it by hand') }
  }
  return (
    <div className="cmd">
      <code>{text}</code>
      <button type="button" className="cmd-copy" onClick={copy}
        aria-label="Copy this command">{done ? 'Copied' : 'Copy'}</button>
    </div>
  )
}

function Step({ n, title, children, done }: {
  n: number; title: string; children: React.ReactNode; done?: boolean
}) {
  return (
    <section className={`wstep${done ? ' done' : ''}`}>
      <span className="wstep-n">{done ? '✓' : n}</span>
      <div className="wstep-body">
        <h4>{title}</h4>
        {children}
      </div>
    </section>
  )
}

function WebAddress({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const [state, setState] = useState<HostingState | null>(null)
  const [url, setUrl] = useState('')
  const [tunnelId, setTunnelId] = useState('')
  const [busy, setBusy] = useState<'' | 'save' | 'config' | 'check' | 'auto' | 'precheck'>('')
  const [check, setCheck] = useState<{ ok: boolean; reason: string } | null>(null)
  const [warn, setWarn] = useState('')
  // Held in component state only, never persisted anywhere: this token can edit
  // DNS and create tunnels across the whole zone. The server uses it for a handful
  // of calls and drops it too.
  const [cfToken, setCfToken] = useState('')
  const [autoNote, setAutoNote] = useState('')
  const [autoOk, setAutoOk] = useState(false)
  const [autoStep, setAutoStep] = useState('')
  const [installed, setInstalled] = useState(true)

  const load = useCallback(async () => {
    try {
      const s = await api<HostingState>('/api/hosting')
      setState(s)
      setUrl(s.from_env ? '' : s.public_url.replace(/^https?:\/\//, ''))
      setTunnelId(s.tunnel_id)
      // Whether cloudflared is present decides if the automatic path can finish
      // by itself or has to hand the person one command first.
      api<{ tunnel: { installed: boolean } }>('/api/hosting/always-on')
        .then((a) => setInstalled(a.tunnel?.installed !== false))
        .catch(() => { })
    } catch (e) { toast(errorMessage(e, 'Could not load the web address')) }
  }, [toast])
  useEffect(() => { load() }, [load])

  // Every command below is built from what has been typed, so the person is never
  // left holding a <placeholder> they have to replace themselves.
  const host = url.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '').toLowerCase()
  const tunnelName = (appName() || 'safenest').toLowerCase().replace(/[^a-z0-9-]/g, '') || 'safenest'
  const idOrPlaceholder = tunnelId.trim() || 'PASTE-THE-ID-FROM-STEP-6'
  const hostOrPlaceholder = /^([a-z0-9-]+\.)+[a-z]{2,}$/.test(host)
    ? host
    // Built from the app's own name, like the tunnel name above it. Hard-coding
    // an example address put a product name the branding screen cannot reach in
    // front of a customer, on their own machine.
    : `${tunnelName}.yourdomain.com`
  const hasHost = /^([a-z0-9-]+\.)+[a-z]{2,}$/.test(host)
  const live = !!(state?.public_url && !state.from_env)

  /** Confirm the token and the domain fit together before creating anything. */
  async function autoCheck() {
    setBusy('precheck'); setAutoNote(''); setAutoOk(false)
    try {
      const r = await api<{ zone: string; hostname: string; status: string }>(
        '/api/hosting/auto/check',
        { method: 'POST', body: { public_url: host, cf_api_token: cfToken.trim() } })
      setAutoOk(true)
      setAutoNote(`✓ Ready — ${r.hostname} sits under ${r.zone}, which is active in Cloudflare.`)
    } catch (e) {
      setAutoOk(false)
      setAutoNote(errorMessage(e, 'Could not check that'))
    } finally { setBusy('') }
  }

  async function autoSetup() {
    if (!hasHost) { toast(`Enter your address first, like ${tunnelName}.yourdomain.com`); return }
    setBusy('auto'); setAutoNote(''); setAutoOk(false); setWarn('')
    setAutoStep('Talking to Cloudflare…')
    try {
      const r = await api<HostingState & {
        created: boolean; zone: string; licences_on_old_url: number
        connector: { running: boolean; installed: boolean }
      }>('/api/hosting/auto',
        { method: 'POST', body: { public_url: host, cf_api_token: cfToken.trim() } })
      setState(r)
      setTunnelId(r.tunnel_id)
      setCfToken('')          // used once; nothing keeps it afterwards
      setInstalled(r.connector?.installed !== false)
      setAutoOk(true)
      setAutoNote(r.connector?.running
        ? `✓ Done. ${r.hostname} is set up and the connector is running. Give DNS a minute, then test below.`
        : `✓ Set up at ${r.hostname}. The connector is not running yet — install it (step 5) and it will start on its own.`)
      if (r.licences_on_old_url > 0) {
        setWarn(`${r.licences_on_old_url} licence(s) were issued against the old address and still call it. Keep the old address working, or issue them again.`)
      }
      toast('Web address set up')
    } catch (e) {
      setAutoOk(false)
      setAutoNote(errorMessage(e, 'Could not set that up'))
    } finally { setBusy(''); setAutoStep('') }
  }

  async function saveAll() {
    if (!hasHost) { toast(`Enter your address first, like ${tunnelName}.yourdomain.com`); return }
    setBusy('save'); setWarn('')
    try {
      const r = await api<HostingState & { previous_url: string; licences_on_old_url: number }>(
        '/api/hosting', { method: 'PUT', body: { public_url: host } })
      if (tunnelId.trim()) {
        await api('/api/hosting/tunnel', {
          method: 'PUT', body: { tunnel_id: tunnelId.trim(), hostname: host },
        })
      }
      setState(r)
      if (r.licences_on_old_url > 0) {
        const many = r.licences_on_old_url === 1
        setWarn(`${r.licences_on_old_url} licensed ${many ? 'copy is' : 'copies are'} still `
          + `pointed at ${r.previous_url}. Keep that address working, or issue those `
          + `licences again - it is written inside each signed licence and cannot be `
          + `changed from here.`)
      }
      toast(`Saved - your address is ${host}`)
      load()
    } catch (e) { toast(errorMessage(e, 'Could not save that address')) }
    finally { setBusy('') }
  }

  async function writeConfig() {
    setBusy('config')
    try {
      const r = await api<HostingState & { note: string }>('/api/hosting/config', { method: 'POST' })
      setState(r)
      toast(r.note || 'Written. Now restart the connector.')
    } catch (e) { toast(errorMessage(e, 'Could not write the config file')) }
    finally { setBusy('') }
  }

  async function runCheck() {
    setBusy('check'); setCheck(null)
    try {
      setCheck(await api<{ ok: boolean; reason: string }>('/api/hosting/check', { method: 'POST' }))
    } catch (e) { toast(errorMessage(e, 'Could not test the address')) }
    finally { setBusy('') }
  }

  return (
    <Sheet title="Your web address" onClose={onClose}>
      {/* Where things stand right now, before any instructions. */}
      <div className={`wnow${live ? ' on' : ''}`}>
        <span className="wnow-dot" aria-hidden="true" />
        <div className="wnow-t">
          <b>{live ? state!.hostname : 'Not published yet'}</b>
          <span>{live
            ? 'Reachable from outside your home network.'
            : `${appName()} works on this computer and on your home Wi-Fi. Follow the steps below to reach it from anywhere.`}</span>
        </div>
      </div>

      <div className="wneed">
        <b>What you need</b>
        <ul>
          {/* No price and no particular extension: a figure quoted here goes out
              of date, and every domain works the same for this. The only useful
              guidance is "you do not need an expensive one". */}
          <li>A domain name — pick whichever is cheapest, they all work the same</li>
          <li>A free Cloudflare account — the free plan is all this needs</li>
          <li>About 15 minutes, plus waiting for the domain to switch over</li>
        </ul>
      </div>

      <Step n={1} title="Add your domain to Cloudflare">
        <p>
          Sign in at <b>dash.cloudflare.com</b>, choose <b>Add a site</b>, type your
          domain and pick the <b>Free</b> plan. Cloudflare then shows you two
          nameserver addresses - keep that page open.
        </p>
      </Step>

      <Step n={2} title="Point the domain at Cloudflare">
        <p>
          Sign in wherever you bought the domain (GoDaddy, Namecheap, BigRock and so
          on), find the <b>Nameservers</b> setting, and replace what is there with
          the two Cloudflare just gave you. This takes anywhere from five minutes to
          a day; Cloudflare emails you when it is ready.
        </p>
      </Step>

      <Step n={3} title="Choose your address" done={hasHost}>
        <p>What would you like to type into a browser to reach {appName()}?</p>
        <Field label="Your address">
          <input className="input" value={url} placeholder={`${tunnelName}.yourdomain.com`}
            autoCapitalize="off" autoCorrect="off" spellCheck={false}
            onChange={(e) => setUrl(e.target.value)} />
        </Field>
        {url.trim() && !hasHost && (
          <p className="form-hint warn">
            That does not look like a domain yet. It should read like
            <b> {tunnelName}.yourdomain.com</b> - no https, no slashes.
          </p>
        )}
        {state?.from_env && (
          <p className="form-hint">
            This copy currently uses <b>{state.env_url}</b> from its configuration
            file. Saving here replaces it.
          </p>
        )}
      </Step>

      {/* Steps 1–3 need a person: nobody but the owner can add a domain to their
          Cloudflare account or change nameservers at the registrar. Everything
          after that is Cloudflare's API, so it is done here rather than typed into
          a terminal — which is what this product exists to spare people. The
          manual steps stay below for anyone who would rather do it themselves, or
          whose token cannot be created. */}
      <Step n={4} title="Let it set the rest up for you" done={!!state?.tunnel_id}>
        <p>
          One token, and {appName()} creates the tunnel, points your address at it
          and starts the connector. The token is used once and not saved.
        </p>
        <details className="wauto-help">
          <summary>Where do I get the token?</summary>
          <p>
            In Cloudflare: <b>My Profile → API Tokens → Create Token → Create
            Custom Token</b>. Give it exactly these two permissions:
          </p>
          <ul>
            <li>Zone → DNS → Edit</li>
            <li>Account → Cloudflare Tunnel → Edit</li>
          </ul>
          <p>
            Copy it when it is shown — Cloudflare will not show it again. Paste the
            long token itself, not the short id beside it.
          </p>
        </details>
        <Field label="Cloudflare API token">
          <input className="input" type="password" value={cfToken}
            placeholder="Paste the token here" autoCapitalize="off"
            autoCorrect="off" spellCheck={false}
            onChange={(e) => setCfToken(e.target.value)} />
        </Field>
        <div className="brand-actions">
          <button className="btn" disabled={busy !== '' || !hasHost || !cfToken.trim()}
            onClick={autoSetup}>
            {busy === 'auto' ? (autoStep || 'Setting up…') : 'Set up my address'}
          </button>
          <button className="btn ghost" disabled={busy !== '' || !hasHost || !cfToken.trim()}
            onClick={autoCheck}>
            {busy === 'precheck' ? 'Checking…' : 'Just check it first'}
          </button>
        </div>
        {autoNote && (
          <p className={`form-hint ${autoOk ? 'ok' : 'warn'}`} style={{ marginTop: 10 }}>
            {autoNote}
          </p>
        )}
        {!installed && (
          <p className="form-hint warn" style={{ marginTop: 10 }}>
            The Cloudflare connector is not on this computer yet. Install it first —
            it is a single command, in step 5 below.
          </p>
        )}
      </Step>

      <details className="wmanual">
        <summary>Or do it yourself, step by step</summary>

        <Step n={5} title="Install the Cloudflare connector">
          <p>
            Open <b>PowerShell</b> on this computer and run this. It is a small
            program that lets your address reach this machine without opening
            anything on your router.
          </p>
          <Cmd text="winget install --id Cloudflare.cloudflared" />
        </Step>

        <Step n={6} title="Sign the connector in">
          <p>This opens your browser. Pick the domain you added in step 1 and approve it.</p>
          <Cmd text="cloudflared tunnel login" />
        </Step>

        <Step n={7} title="Create the tunnel" done={!!tunnelId.trim()}>
          <p>It prints a line ending in an id. Copy that id into the box below.</p>
          <Cmd text={`cloudflared tunnel create ${tunnelName}`} />
          <Field label="Tunnel id">
            <input className="input" value={tunnelId}
              placeholder="b6ea7271-4d37-414e-9899-55be7f3903c5"
              autoCapitalize="off" autoCorrect="off" spellCheck={false}
              onChange={(e) => setTunnelId(e.target.value)} />
          </Field>
        </Step>

        <Step n={8} title="Point your address at the tunnel">
          <p>This creates the DNS record inside Cloudflare for you.</p>
          <Cmd text={`cloudflared tunnel route dns ${idOrPlaceholder} ${hostOrPlaceholder}`} />
        </Step>

        <Step n={9} title="Save it here, then restart the connector">
          <div className="brand-actions">
            <button className="btn" disabled={busy !== '' || !hasHost} onClick={saveAll}>
              {busy === 'save' ? 'Saving…' : 'Save address'}
            </button>
            <button className="btn ghost" disabled={busy !== '' || !state?.tunnel_id}
              onClick={writeConfig}>
              {busy === 'config' ? 'Writing…' : 'Write config file'}
            </button>
          </div>
          {warn && <p className="form-hint warn" style={{ marginTop: 10 }}>{warn}</p>}
          {state?.config_written && (
            <>
              <p className="form-hint">Written to <code>{state.config_path}</code>. Now run:</p>
              <Cmd text="net stop cloudflared" />
              <Cmd text="net start cloudflared" />
            </>
          )}
        </Step>
      </details>

      <Step n={10} title="Check it works">
        <button className="btn ghost block" disabled={busy !== '' || !state?.public_url}
          onClick={runCheck}>
          {busy === 'check' ? 'Testing…' : 'Test my address'}
        </button>
        {check && (
          <p className={`form-hint ${check.ok ? 'ok' : 'warn'}`} style={{ marginTop: 8 }}>
            {check.ok ? '✓ ' : '• '}{check.reason}
          </p>
        )}
        <p className="form-hint">
          If it does not answer straight away, give the domain a few more minutes
          and try again. DNS is usually the slow part.
        </p>
      </Step>

      <p className="form-hint" style={{ marginTop: 18 }}>
        You can change this address later. Anything already installed elsewhere
        keeps using the address it was given, so leave the old one working if other
        people rely on it.
      </p>
    </Sheet>
  )
}

/** The "Web address" entry in Profile.
 *
 *  Deliberately NOT inside the Administration group. A licensed customer is given
 *  the `user` role on purpose - they administer nothing of the publisher's - but
 *  the address their own copy answers on is theirs to set. Gating this on the
 *  admin role would hide it from exactly the people it is for, since a customer's
 *  copy has no administrator at all.
 *
 *  The server decides; this asks and renders nothing if the answer is no.
 */
function WebAddressSection({ onOpen }: { onOpen: () => void }) {
  const [state, setState] = useState<{ can_manage?: boolean; hostname?: string } | null>(null)

  useEffect(() => {
    api<{ can_manage?: boolean; hostname?: string }>('/api/hosting')
      .then(setState)
      .catch(() => setState(null))
  }, [])

  if (!state?.can_manage) return null

  return (
    <SettingsGroup title="Reaching this app"
      footer={`Set your own domain so you can open ${appName()} from anywhere, not just on this computer. Step-by-step, including what to do in Cloudflare.`}>
      <SettingsRow icon="🌐" tint="var(--c-insurance)" label="Web address"
        sub={state.hostname || 'Not published yet'} onClick={onOpen} />
    </SettingsGroup>
  )
}

/** Watch a folder, and import whatever appears in it.
 *
 *  Not the iPhone answer on its own — something still has to put photos in the
 *  folder — but it is the whole answer for photos that are already on this
 *  computer, for plugging the phone in, and for anything that syncs into a folder
 *  later. It sits below the phone route because that is the question people
 *  arrive with.
 */
function WatchFolderSection() {
  const toast = useToast()
  const [st, setSt] = useState<{
    folder: string; enabled: boolean; imported: number; skipped: number
    last_scan_at: string | null; last_error: string
  } | null>(null)
  const [folder, setFolder] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<NonNullable<typeof st>>('/api/autoimport')
      .then((d) => { setSt(d); setFolder(d.folder || '') })
      .catch(() => setSt(null))
  }, [])
  useEffect(() => { load() }, [load])
  // While it is on, the count is the only sign it is working.
  useEffect(() => {
    if (!st?.enabled) return
    const t = window.setInterval(load, 10000)
    return () => window.clearInterval(t)
  }, [st?.enabled, load])

  async function save(enabled: boolean) {
    setBusy(true)
    try {
      if (enabled && folder.trim()) {
        const c = await api<{ photos: number }>('/api/autoimport/check', {
          method: 'POST', body: { folder },
        })
        toast(c.photos
          ? `Found ${c.photos.toLocaleString()} photo${c.photos === 1 ? '' : 's'} — importing them now`
          : 'That folder has no photos in it yet — anything added later will come in')
      }
      const d = await api<NonNullable<typeof st>>('/api/autoimport', {
        method: 'POST', body: { folder, enabled },
      })
      setSt(d)
      if (!enabled) toast('Stopped watching that folder')
    } catch (e) { toast(errorMessage(e)) }
    finally { setBusy(false) }
  }

  if (!st) return null
  return (
    <SettingsGroup title="Import from a folder"
      footer="Anything that appears in this folder is added to your gallery, on its own, for as long as it is switched on. Photos already here are skipped.">
      <SettingsBlock>
        <Field label="Folder on this computer">
          <input className="input" value={folder} onChange={(e) => setFolder(e.target.value)}
            placeholder="D:\Photos\iPhone" spellCheck={false} />
        </Field>
        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <button className="btn sm" style={{ flex: 1 }} disabled={busy || !folder.trim()}
            onClick={() => save(true)}>
            {st.enabled ? 'Save' : 'Start watching'}
          </button>
          {st.enabled && (
            <button className="btn sm ghost" disabled={busy} onClick={() => save(false)}>
              Stop
            </button>
          )}
        </div>
        {st.enabled && (
          <p style={{ color: 'var(--ink-soft)', fontSize: 13, margin: '10px 0 0' }}>
            {st.imported.toLocaleString()} imported
            {st.skipped ? `, ${st.skipped.toLocaleString()} already here or unreadable` : ''}
            {st.last_scan_at ? ` · last looked ${st.last_scan_at}` : ' · not looked yet'}
          </p>
        )}
        {!!st.last_error && (
          <p style={{ color: 'var(--danger)', fontSize: 13, margin: '8px 0 0' }}>{st.last_error}</p>
        )}
      </SettingsBlock>
    </SettingsGroup>
  )
}

interface DeviceRow {
  id: number; name: string; prefix: string; uploads: number
  created_at: string; last_used_at: string | null; revoked: boolean
}

/** Backing up an iPhone's photos without going through the iPhone's browser.
 *
 *  A web page cannot read the photo library. The file picker is the only door,
 *  and above roughly a hundred photos iOS stops closing it — measured on a real
 *  phone, the same in a Safari tab and in the Home-Screen copy, with local photos
 *  and no format conversion involved. So the whole-library backup people actually
 *  want cannot be built out of a file input, however it is presented.
 *
 *  The Shortcuts app has the access the browser is denied. This screen hands over
 *  the two things a shortcut needs — an address and a token — and says, in order,
 *  which buttons to press on the phone.
 */
function PhoneBackupSection() {
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<DeviceRow[]>([])

  const load = useCallback(() => {
    api<{ devices: DeviceRow[] }>('/api/devices')
      .then((d) => setRows(d.devices || []))
      .catch(() => setRows([]))
  }, [])
  useEffect(() => { load() }, [load])

  const live = rows.filter((r) => !r.revoked)
  return (
    <>
      <SettingsGroup title="Photos from your phone"
        footer="A web page cannot read an iPhone's photo library, so a whole-gallery backup has to come from the phone's own Shortcuts app. This sets that up.">
        <SettingsRow icon="📲" tint="var(--c-gallery, var(--c-investments))"
          label="Back up from an iPhone"
          sub={live.length
            ? `${live.length} phone${live.length === 1 ? '' : 's'} set up`
            : 'Not set up yet'}
          onClick={() => setOpen(true)} />
      </SettingsGroup>
      {open && <PhoneBackupSheet rows={rows} reload={load} onClose={() => setOpen(false)} />}
    </>
  )
}

function PhoneBackupSheet({ rows, reload, onClose }: {
  rows: DeviceRow[]; reload: () => void; onClose: () => void
}) {
  const toast = useToast()
  const [secret, setSecret] = useState('')
  const [ready, setReady] = useState('')     // the built shortcut, if there is one
  const [name, setName] = useState('My iPhone')
  const [busy, setBusy] = useState(false)
  const [host, setHost] = useState('')

  useEffect(() => {
    api<{ hostname?: string }>('/api/hosting')
      .then((d) => setHost(d.hostname || ''))
      .catch(() => setHost(''))
  }, [])

  // The address the PHONE must call, which is not necessarily the one this
  // browser is using: setting this up on the computer itself means the origin is
  // 127.0.0.1, and a phone typing that reaches only itself.
  const base = host
    ? (host.startsWith('http') ? host : `https://${host}`)
    : window.location.origin
  const localOnly = /^https?:\/\/(127\.0\.0\.1|localhost)/i.test(base)

  async function create() {
    setBusy(true)
    try {
      const made = await api<{ token: string; shortcut_url?: string }>('/api/devices', {
        method: 'POST', body: { name: name.trim() || 'My iPhone' },
      })
      setSecret(made.token)
      setReady(made.shortcut_url || '')
      reload()
    } catch (e) { toast(errorMessage(e)) }
    finally { setBusy(false) }
  }

  async function revoke(id: number) {
    try {
      await api(`/api/devices/${id}`, { method: 'DELETE' })
      toast('That phone can no longer send photos')
      reload()
    } catch (e) { toast(errorMessage(e)) }
  }

  return (
    <Sheet title="Back up from an iPhone" onClose={onClose}>
      <p style={{ color: 'var(--ink)', fontSize: 14, lineHeight: 1.6, marginTop: 4 }}>
        <b>Set this up once and you never pick photos again.</b> It takes your whole
        gallery — all of it, however many — and it can run by itself every night
        while the phone charges. No selecting, no fifty at a time, no picker.
      </p>
      <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.55, marginTop: 10 }}>
        It has to work this way because Safari is not allowed to read your photo
        library at all, and its file picker gives up somewhere above a hundred
        photos. The phone&rsquo;s own Shortcuts app has neither limit.
      </p>
      {/* Said before step 1, because a 43-character token typed by hand from
          another screen is where this stops being worth doing. */}
      <p style={{ color: 'var(--ink)', fontSize: 13, lineHeight: 1.55, marginTop: 10,
                  background: 'var(--bg)', border: '1px solid var(--line)',
                  borderRadius: 12, padding: '10px 12px' }}>
        📱 <b>Do this on the iPhone itself.</b> Open {appName()} on the phone and
        come back to this screen there — then the Copy buttons put the address and
        the token straight on the phone&rsquo;s clipboard, ready to paste into
        Shortcuts. Setting it up here on the computer means copying them across by
        hand.
      </p>

      {localOnly && (
        <p style={{ color: 'var(--danger)', fontSize: 13, lineHeight: 1.55, marginTop: 12 }}>
          This computer has no web address yet, so the only address available is
          this one — which on your phone would mean the phone itself. Set one up
          under <b>Reaching this app</b> first, or use the computer&rsquo;s address
          on your Wi-Fi.
        </p>
      )}

      <Step n={1} title="Make a token for this phone">
        <Field label="What is this phone called?">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="My iPhone" maxLength={60} />
        </Field>
        {!secret ? (
          <button className="btn block" style={{ marginTop: 10 }} onClick={create} disabled={busy}>
            {busy ? 'Creating…' : 'Create a token'}
          </button>
        ) : (
          <>
            {ready && (
              <>
                {/* TWO WAYS IN, because which one works depends on the iOS
                    version and there is no way to ask from here. The plain link
                    is the more reliable of the two: Safari downloads the file and
                    offers it to Shortcuts. The URL scheme is tidier when it is
                    allowed, and is refused outright on some versions. */}
                <a className="btn block" style={{ marginTop: 10 }} href={ready}>
                  ⚡ Add the shortcut to this iPhone
                </a>
                <p style={{ color: 'var(--ink-soft)', fontSize: 12, lineHeight: 1.55,
                            margin: '8px 0 0' }}>
                  Tap that <b>on the iPhone</b>. Safari downloads it and offers to
                  open it in Shortcuts, already built, with your token in it.
                </p>
                <a className="btn ghost block" style={{ marginTop: 8 }}
                   href={`shortcuts://import-shortcut?url=${encodeURIComponent(ready)}&name=${encodeURIComponent('Back up to ' + appName())}`}>
                  Open Shortcuts directly instead
                </a>
                <p style={{ color: 'var(--ink-soft)', fontSize: 12, lineHeight: 1.55,
                            margin: '8px 0 0' }}>
                  If either says the shortcut cannot be opened, turn on
                  {' '}<b>Settings → Shortcuts → Allow Untrusted Shortcuts</b> and
                  tap again. Both links last fifteen minutes; after that, make a
                  new token. The steps below always work.
                </p>
              </>
            )}
            <p style={{ color: 'var(--ink-soft)', fontSize: 13, margin: '12px 0 6px' }}>
              Your token, if you are building it by hand. Shown once and never
              again — if you lose it, make another and revoke this one.
            </p>
            <Cmd text={secret} />
          </>
        )}
      </Step>

      <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.55,
                  margin: '16px 0 0' }}>
        The steps below build the same thing by hand. Skip them if the button
        above worked.
      </p>

      <Step n={2} title="On the iPhone, open Shortcuts and make a new one">
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6 }}>
          Tap <b>+</b>, then <b>Add Action</b>. Search for <b>Find Photos</b> and add
          it. <b>Leave it with no filter and no limit</b> — that is what makes it
          take the entire gallery rather than a selection. If you would rather
          check it works first, add <b>Limit</b> and set it to 5, then remove it.
        </p>
      </Step>

      <Step n={3} title="Add “Repeat with Each”">
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6 }}>
          Search for <b>Repeat with Each</b> and add it below. It should say
          {' '}<i>Repeat with each item in Photos</i>. Everything next goes
          {' '}<b>inside</b> the repeat.
        </p>
      </Step>

      <Step n={4} title="Add “Get Contents of URL”">
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6, marginBottom: 8 }}>
          Add it inside the repeat, then tap <b>Show More</b> and set it up exactly
          like this. The address:
        </p>
        <Cmd text={`${base.replace(/\/$/, '')}/api/devices/upload`} />
        <ul style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.8,
                     paddingLeft: 18, margin: '10px 0 0' }}>
          <li><b>Method:</b> POST</li>
          <li><b>Headers:</b> one header, key <code>Authorization</code>, value
            {' '}<code>Bearer </code> followed by the token from step 1.</li>
          <li><b>Request Body:</b> Form</li>
          <li>One field — key <code>file</code>, type <b>File</b>, value
            {' '}<b>Repeat Item</b>.</li>
        </ul>
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6, marginTop: 10 }}>
          The word <b>Bearer</b>, then a space, then the token — the space matters.
        </p>
      </Step>

      <Step n={5} title="Name it and run it">
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6 }}>
          Call it <b>Back up to {appName()}</b> and tap play. It will work through
          the whole library on its own — leave the phone alone while it does. The
          count on this screen goes up as they land.
        </p>
      </Step>

      <Step n={6} title="Make it automatic — this is the part that matters">
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6 }}>
          In Shortcuts open the <b>Automation</b> tab, tap <b>+</b>, choose
          {' '}<b>Charger</b> (or <b>Time of Day</b>), pick <b>Run Immediately</b>
          {' '}and turn <b>Notify When Run</b> off. Point it at the shortcut you
          just made.
        </p>
        <p style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.6, marginTop: 8 }}>
          From then on every new photo arrives on its own, and you never open this
          again. Ones already here are skipped, so running it nightly costs almost
          nothing.
        </p>
      </Step>

      {rows.length > 0 && (
        <>
          <h4 style={{ margin: '18px 0 8px', fontSize: 14 }}>Phones set up</h4>
          {rows.map((r) => (
            <div key={r.id} className="set-row" style={{ alignItems: 'center' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14 }}>
                  {r.name} <code style={{ opacity: 0.6 }}>{r.prefix}…</code>
                </div>
                <div style={{ color: 'var(--ink-soft)', fontSize: 12 }}>
                  {r.revoked ? 'Revoked' : `${r.uploads.toLocaleString()} photo${r.uploads === 1 ? '' : 's'} sent`}
                  {r.last_used_at ? ` · last ${r.last_used_at}` : ' · never used'}
                </div>
              </div>
              {!r.revoked && (
                <button className="btn sm ghost" onClick={() => revoke(r.id)}>Revoke</button>
              )}
            </div>
          ))}
        </>
      )}

      <p style={{ color: 'var(--ink-soft)', fontSize: 12, lineHeight: 1.6, marginTop: 16 }}>
        A token can only send a photo in. It cannot read your photos, your vault or
        anything else in {appName()}, and revoking it stops it at once.
      </p>
    </Sheet>
  )
}

/** "Act as my server" — start with the computer, and keep the tunnel up.
 *
 *  This is the setting that makes the product's central promise true: your
 *  records are reachable from anywhere while your computer is on. Without it the
 *  app only runs when somebody double-clicks it, so a reboot silently takes the
 *  address down and there is no obvious reason why.
 *
 *  Two states are reported separately on purpose. "Starts at login" and "tunnel
 *  running" are different things, and a combined on/off would hide the
 *  half-configured case — which is the one that actually causes trouble.
 */
interface AlwaysOn {
  startup: { supported: boolean; enabled: boolean; platform: string; path?: string; reason?: string }
  tunnel: { installed: boolean; configured: boolean; running: boolean; reason: string }
}

interface UpdateState {
  running: string
  available: boolean
  installable: boolean
  version?: string
  notes?: string
  size_mb?: number
  reason?: string
}

/** A new version of the installed program, offered by whoever supplied it.
 *
 * Distinct from "Refresh the web app" above, which only reloads this page's files.
 * This replaces the executable, so it is never automatic: an app that swaps itself
 * unasked, on a machine holding somebody's financial records, is the behaviour
 * people are rightly taught to refuse. Nothing is downloaded until it is pressed.
 */
interface InstallProgress {
  state: 'idle' | 'downloading' | 'checking' | 'unpacking' | 'restarting' | 'failed'
  percent: number
  note: string
  version?: string
}

/** Remembered across the restart, because the page it was shown on is gone.
 *
 *  The app replaces itself and reopens, so nothing in memory survives to say the
 *  update worked. Without this the new version simply appears, which is
 *  indistinguishable from having pressed the button and nothing happening.
 */
const INSTALLED_KEY = 'app.updated.to'

/** Is the app that just answered actually the version we installed?
 *
 *  Straight equality is too strict — a manifest saying "2.1" against a build that
 *  stamps "2.1.0" would never match and the bar would sit there until it gave up.
 *  Being too loose is the worse failure though: announcing success while the old
 *  program is still the one answering. Hence a numeric compare, where reaching the
 *  target or passing it counts and falling short does not.
 */
function updatedTo(running: string, target?: string): boolean {
  if (!target) return false
  const parts = (v: string) => String(v || '0').split('.')
    .map((c) => parseInt(c.replace(/\D/g, ''), 10) || 0)
  const a = parts(running), b = parts(target)
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] || 0, y = b[i] || 0
    if (x !== y) return x > y
  }
  return true
}

function AppUpdateRow() {
  const toast = useToast()
  const [st, setSt] = useState<UpdateState | null>(null)
  const [busy, setBusy] = useState<'' | 'check' | 'install'>('')
  const [prog, setProg] = useState<InstallProgress | null>(null)

  /** Check for a newer version. `announce` when a person asked, not on load.
   *
   *  Tapping this used to re-check and say nothing: the request is fast, the row
   *  redraws identically, and the only sign anything happened was a "Checking…"
   *  that flashed past. Reported as "I clicked it and there was no action" —
   *  which was exactly right.
   */
  const look = useCallback(async (announce = false) => {
    setBusy('check')
    try {
      const next = await api<UpdateState>('/api/update')
      setSt(next)
      if (announce) {
        toast(next.available
          ? `Version ${next.version} is ready — tap to install`
          : next.reason || `You are on the latest version (${next.running})`)
      }
    } catch (e) {
      setSt(null)
      if (announce) toast(errorMessage(e, 'Could not check for updates'))
    } finally { setBusy('') }
  }, [toast])
  useEffect(() => { look() }, [look])

  // The install survived a restart, so say so on the page that came back.
  useEffect(() => {
    const done = localStorage.getItem(INSTALLED_KEY)
    if (done) {
      localStorage.removeItem(INSTALLED_KEY)
      toastRef.current(`Updated to version ${done} — you are on the newest version`)
    }
    // Once, on mount. Depending on `toast` re-ran this on every render.
  }, [])

  /** Follow an install through to the app reopening.
   *
   *  WHY THIS IS KEYED ON A STRING AND NOT ON `prog`
   *  It used to depend on the `prog` OBJECT and on `toast`. Both get a new
   *  identity on every render, so every render tore the effect down — setting
   *  `alive = false` and clearing the pending timer — and scheduled a fresh first
   *  tick 1.5s later. Anything re-rendering this screen faster than that meant
   *  the first tick never fired at all.
   *
   *  So the bar sat at 0% saying "Starting the download…" while the download was
   *  running perfectly well underneath it, and pressing the button again
   *  correctly answered "an update is already being installed". Reported exactly
   *  that way, and it reads as a hung update rather than a screen that stopped
   *  looking.
   *
   *  `phase` is a string. It changes when the install genuinely moves on, and not
   *  when React happens to re-render.
   *
   *  Two halves, and the join between them is the awkward part: while the server
   *  is alive it reports its own progress, but the last act of an install is to
   *  kill that server, so from "restarting" onwards a failed request is the
   *  expected thing rather than an error. The poll keeps going until the app
   *  answers again on the new version.
   */
  const progRef = useRef<InstallProgress | null>(null)
  progRef.current = prog
  const toastRef = useRef(toast)
  toastRef.current = toast
  const phase = prog && prog.state !== 'idle' && prog.state !== 'failed'
    ? `${prog.state}:${prog.version || ''}`
    : ''

  useEffect(() => {
    if (!phase) return
    let alive = true
    let waited = 0

    const tick = async () => {
      if (!alive) return
      const cur = progRef.current
      if (!cur) return
      if (cur.state === 'restarting') {
        waited += 2
        try {
          const back = await api<UpdateState>('/api/update')
          // Answering on the version we installed means the swap completed and
          // this is the new program talking. Anything else is the old one not yet
          // gone, so keep waiting.
          if (!updatedTo(back.running, cur.version)) throw new Error('not yet')
          localStorage.setItem(INSTALLED_KEY, back.running)
          location.reload()
          return
        } catch {
          if (waited > 180) {
            setProg({ ...cur, state: 'failed', percent: 0,
                      note: `${appName()} is taking longer than expected to reopen. `
                          + 'If it does not come back, open it yourself — your '
                          + 'records are safe either way.' })
            return
          }
        }
      } else {
        try {
          const p = await api<InstallProgress>('/api/update/progress')
          if (alive) setProg(p)
        } catch { /* a blip mid-download; the next tick asks again */ }
      }
      if (alive) timer = setTimeout(tick, 2000)
    }
    // Held in one variable so the cleanup cancels whichever tick is pending,
    // not only the first. Leaking a timer here would leave two polls running.
    let timer = setTimeout(tick, 1200)
    return () => { alive = false; clearTimeout(timer) }
  }, [phase])

  if (!st || !st.installable) return null

  async function install() {
    if (!confirm(`Install version ${st!.version}?

${appName()} will download it, then close and reopen. Your records are not touched.`)) return
    setBusy('install')
    setProg({ state: 'downloading', percent: 0, note: 'Starting the download…',
              version: st!.version })
    try {
      await api('/api/update', { method: 'POST' })
    } catch (e) {
      toast(errorMessage(e, 'Could not install the update'))
      setBusy(''); setProg(null)
    }
  }

  if (prog && prog.state !== 'idle') {
    const failed = prog.state === 'failed'
    return (
      <div className="card" style={{ padding: '14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {!failed && <div className="upbar-spin" />}
          <div style={{ fontWeight: 700, fontSize: 14 }}>
            {failed ? 'Update stopped' : `Installing version ${prog.version || st.version}`}
          </div>
          {!failed && (
            <div style={{ marginLeft: 'auto', fontWeight: 700, fontSize: 13,
                          color: 'var(--brand)' }}>{prog.percent}%</div>
          )}
        </div>
        {!failed && (
          <div className="upbar-track"><i style={{ width: `${prog.percent}%` }} /></div>
        )}
        <div className="upbar-sub" style={failed ? { color: 'var(--danger)' } : undefined}>
          {prog.note}
        </div>
        {failed
          ? <button className="btn ghost" style={{ marginTop: 10 }}
              onClick={() => { setProg(null); setBusy(''); look() }}>Close</button>
          // A way out of an install that is going nowhere. Without it a stalled
          // download answers "already being installed" to every later press and
          // the only escape is quitting the app, which reads as the update having
          // broken the program.
          : prog.state !== 'restarting' && (
            <button className="btn ghost" style={{ marginTop: 10 }}
              onClick={async () => {
                try { await api('/api/update/cancel', { method: 'POST' }) } catch { /* already gone */ }
                setProg(null); setBusy(''); look()
              }}>Stop</button>
          )}
      </div>
    )
  }

  if (st.available) {
    return (
      <SettingsRow icon="⬆" tint="var(--ok)"
        label={busy === 'install' ? 'Starting…' : `Version ${st.version} is ready`}
        sub={st.notes || `${st.size_mb} MB — your records are not affected`}
        onClick={busy ? undefined : install} />
    )
  }
  return (
    <SettingsRow icon="✓" tint="var(--ink-faint)"
      label={busy === 'check' ? 'Checking…' : `App version ${st.running}`}
      sub={st.reason || 'Tap to check for a new version'}
      onClick={busy ? undefined : () => look(true)} />
  )
}

interface RecordsLocation {
  path: string
  can_change: boolean
  size_bytes: number
  free_bytes: number
  reason: string
}

/** Where this copy keeps its records, and moving them somewhere else.
 *
 *  The launcher asks once, on the very first run. That is right for the common
 *  case and wrong for every other one — someone who kept the default and later
 *  bought an external disk, someone whose photos have outgrown a laptop drive,
 *  and the case that prompted this: someone who never saw the question, because
 *  the first-run window did not open and the launcher used its default rather
 *  than blocking the launch on a window that would never appear.
 */
function RecordsLocationRow({ fallback }: { fallback: string }) {
  const [st, setSt] = useState<RecordsLocation | null>(null)
  const [open, setOpen] = useState(false)

  const load = useCallback(() => {
    api<RecordsLocation>('/api/system/records-location').then(setSt).catch(() => setSt(null))
  }, [])
  useEffect(() => { load() }, [load])

  const where = st?.path || fallback
  return (
    <>
      <SettingsRow icon="🗄️" tint="var(--c-investments)" label="Records kept in"
        sub={st && st.size_bytes
          ? `${where} — ${formatBytes(st.size_bytes)}`
          : where}
        onClick={st?.can_change ? () => setOpen(true) : undefined} />
      {open && st && (
        <MoveRecordsSheet st={st} onClose={() => setOpen(false)}
          onMoved={() => { setOpen(false); load() }} />
      )}
    </>
  )
}

function MoveRecordsSheet({ st, onClose, onMoved }: {
  st: RecordsLocation; onClose: () => void; onMoved: () => void
}) {
  const toast = useToast()
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState<'' | 'check' | 'move'>('')
  const [checked, setChecked] = useState<{ need_bytes: number; free_bytes: number
                                           not_empty: boolean } | null>(null)
  const [done, setDone] = useState('')

  async function check() {
    setBusy('check'); setChecked(null)
    try {
      setChecked(await api('/api/system/records-location/check',
        { method: 'POST', body: { path } }))
    } catch (e) { toast(errorMessage(e, 'That folder will not work')) }
    finally { setBusy('') }
  }

  async function move() {
    setBusy('move')
    try {
      const r = await api<{ message: string }>('/api/system/records-location',
        { method: 'POST', body: { path } })
      setDone(r.message)
    } catch (e) { toast(errorMessage(e, 'Could not move the records')); setBusy('') }
  }

  if (done) {
    return (
      <Sheet title="Records copied" onClose={onMoved}>
        <p style={{ color: 'var(--ink-soft)', fontSize: 14, lineHeight: 1.6 }}>{done}</p>
        <button className="btn block" style={{ marginTop: 14 }} onClick={onMoved}>Done</button>
      </Sheet>
    )
  }

  return (
    <Sheet title="Move my records" onClose={onClose}>
      <p style={{ color: 'var(--ink-soft)', fontSize: 13.5, lineHeight: 1.6 }}>
        They are in <b style={{ wordBreak: 'break-all' }}>{st.path}</b>
        {st.size_bytes ? <> — {formatBytes(st.size_bytes)}</> : null}.
      </p>
      <Field label="New folder">
        <input className="input" value={path} placeholder="/Volumes/MyDrive/SafeNest/data"
          autoCapitalize="off" autoCorrect="off" spellCheck={false}
          onChange={(e) => { setPath(e.target.value); setChecked(null) }} />
      </Field>
      <p style={{ color: 'var(--ink-faint)', fontSize: 12.5, lineHeight: 1.55 }}>
        The full path to a folder on this computer or a drive plugged into it.
        It is created if it does not exist.
      </p>

      {checked && (
        <div className="card" style={{ padding: '12px 14px', marginTop: 10 }}>
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>That folder will work</div>
          <div className="upbar-sub">
            Needs {formatBytes(checked.need_bytes)}, {formatBytes(checked.free_bytes)} free.
            {checked.not_empty && ' There are already other files in it; they are left alone.'}
          </div>
        </div>
      )}

      {/* Said before the button, not after. Copying is the safe half; the part
          worth knowing is that nothing changes until the app is restarted, and
          that the old folder is deliberately not deleted. */}
      <ul style={{ color: 'var(--ink-soft)', fontSize: 13, lineHeight: 1.7,
                   paddingLeft: 18, margin: '12px 0 4px' }}>
        <li>Everything is <b>copied</b>. Your current folder is left exactly as it is.</li>
        <li>Close the app and open it again to start using the new folder.</li>
        <li>Delete the old folder yourself, once you have checked it all arrived.</li>
      </ul>

      {!checked
        ? <button className="btn block" style={{ marginTop: 14 }}
            disabled={!path.trim() || busy === 'check'} onClick={check}>
            {busy === 'check' ? 'Checking…' : 'Check this folder'}
          </button>
        : <button className="btn block" style={{ marginTop: 14 }}
            disabled={busy === 'move'} onClick={move}>
            {busy === 'move' ? 'Copying — this can take a while…' : 'Copy my records there'}
          </button>}
      <button className="btn ghost block" style={{ marginTop: 8 }} onClick={onClose}>
        Not now
      </button>
    </Sheet>
  )
}

interface Household {
  people: { id: number; name: string; email: string; role: string; status: string
            initials: string; is_you: boolean; created_at: string | null }[]
  allowed: number
  used: number
  unlimited: boolean
  can_add: boolean
  can_manage: boolean
}

/** The people who can sign in to this copy — the owner and their family.
 *
 * A licensed copy has no administrator by design, so the ordinary user-management
 * screen is unreachable there and the licence holder could not add their own
 * spouse or child. Same arrangement as the web address: allowed in a licensed
 * copy, admin-only on the publisher's own installation. How many is set by the
 * licence, not by anything on this machine.
 */
function HouseholdSection() {
  const toast = useToast()
  const [st, setSt] = useState<Household | null>(null)
  const [open, setOpen] = useState(false)

  const load = useCallback(() => {
    api<Household>('/api/household').then(setSt).catch(() => setSt(null))
  }, [])
  useEffect(() => { load() }, [load])

  if (!st) return null

  const limit = st.unlimited ? 'No limit' : `${st.used} of ${st.allowed}`
  return (
    <>
      <SettingsGroup title="Who can sign in"
        footer={st.unlimited
          ? 'Add sign-ins for anyone in your household. Each person keeps their own records — nobody sees anyone else\'s.'
          : `Your licence covers ${st.allowed} sign-in${st.allowed === 1 ? '' : 's'}. Each person keeps their own records — nobody sees anyone else's.`}>
        <SettingsRow icon="👪" tint="var(--c-people)" label="Family members"
          value={limit} onClick={() => setOpen(true)} />
      </SettingsGroup>
      {open && <HouseholdSheet state={st} onClose={() => setOpen(false)}
        onChanged={load} toast={toast} />}
    </>
  )
}

function HouseholdSheet({ state, onClose, onChanged, toast }: {
  state: Household; onClose: () => void; onChanged: () => void
  toast: (m: string) => void
}) {
  const [st, setSt] = useState(state)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    const next = await api<Household>('/api/household')
    setSt(next)
    onChanged()
  }, [onChanged])

  async function add() {
    setBusy(true)
    try {
      await api('/api/household', {
        method: 'POST',
        body: { name: name.trim(), email: email.trim(), password: pw },
      })
      setName(''); setEmail(''); setPw(''); setAdding(false)
      await reload()
      toast('They can sign in now')
    } catch (e) { toast(errorMessage(e, 'Could not add them')) }
    finally { setBusy(false) }
  }

  async function remove(id: number, who: string) {
    if (!confirm(`Remove ${who}? Their records stay on this computer but they can no longer sign in.`)) return
    setBusy(true)
    try {
      await api(`/api/household/${id}`, { method: 'DELETE' })
      await reload()
      toast('Removed')
    } catch (e) { toast(errorMessage(e, 'Could not remove them')) }
    finally { setBusy(false) }
  }

  const pwBad = pw.length > 0 && pw.length < 12 ? 'At least 12 characters' : ''

  return (
    <Sheet title="Family members" onClose={onClose}>
      <SettingsGroup footer={st.unlimited
        ? 'Your licence has no limit on sign-ins.'
        : `${st.used} of ${st.allowed} used. Ask your supplier if you need more.`}>
        {st.people.map(p => (
          <SettingsRow key={p.id} icon={p.initials} tint="var(--brand)"
            label={p.name + (p.is_you ? ' (you)' : '')} sub={p.email}
            right={p.is_you ? undefined : (
              <button className="btn ghost sm" disabled={busy}
                onClick={() => remove(p.id, p.name)}>Remove</button>
            )} />
        ))}
      </SettingsGroup>

      {!adding && (
        <button className="btn block" disabled={!st.can_add || busy}
          onClick={() => setAdding(true)}>
          {st.can_add ? 'Add someone' : 'No sign-ins left on this licence'}
        </button>
      )}

      {adding && (
        <SettingsBlock>
          <Field label="Their name">
            <input className="input" value={name} autoFocus maxLength={120}
              onChange={(e) => setName(e.target.value)} placeholder="Full name" />
          </Field>
          <Field label="Their email">
            <input className="input" type="email" value={email} autoComplete="off"
              autoCapitalize="off" spellCheck={false}
              onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
          </Field>
          <Field label="A password for them">
            <input className="input" type="password" value={pw} autoComplete="new-password"
              onChange={(e) => setPw(e.target.value)} placeholder="At least 12 characters" />
          </Field>
          {pwBad && <p className="form-hint warn">{pwBad}</p>}
          <p className="form-hint">
            They can change this once they sign in. Their records are their own —
            you will not see them, and they will not see yours.
          </p>
          <div className="brand-actions">
            <button className="btn" disabled={busy || !name.trim() || !email.trim() || !!pwBad || pw.length < 12}
              onClick={add}>{busy ? 'Adding…' : 'Add them'}</button>
            <button className="btn ghost" disabled={busy}
              onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </SettingsBlock>
      )}
    </Sheet>
  )
}

interface LocalNetwork {
  platform: string
  rule: boolean
  private_network: boolean
  networks: { name: string; category: string }[]
  allowed: boolean
  address: string
  advice: string
}

/** Whether a phone on the same Wi-Fi can actually reach this computer.
 *
 * Its own block because it fails invisibly: the launcher prints a phone address,
 * the server really is listening on it, and Windows Firewall drops the connection
 * in between. From the phone that is an ordinary timeout, so nothing anywhere
 * tells the person what is wrong — which is exactly what happened.
 */
function LocalNetworkSection() {
  const toast = useToast()
  const [st, setSt] = useState<LocalNetwork | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<LocalNetwork>('/api/hosting/local-network').then(setSt).catch(() => setSt(null))
  }, [])
  useEffect(() => { load() }, [load])

  // Only Windows has this problem; showing an "allowed" row on a Mac would invent
  // a setting that does not exist there.
  if (!st || st.platform !== 'windows') return null

  async function run(path: string, done: string) {
    setBusy(true)
    try {
      await api(path, { method: 'POST' })
      const next = await api<LocalNetwork>('/api/hosting/local-network')
      setSt(next)
      toast(next.allowed ? 'Phones on your Wi-Fi can reach this computer now' : done)
    } catch (e) { toast(errorMessage(e, 'Windows would not allow it')) }
    finally { setBusy(false) }
  }

  const netName = st.networks[0]?.name || 'this network'

  return (
    <SettingsGroup title="On my Wi-Fi"
      footer="Two things have to be true before a phone on the same Wi-Fi can open this app, and they fail in exactly the same way — the address simply never answers. They are shown separately so you can see which one is missing.">
      <SettingsBlock>
        <div className="ao">
          {/* Windows Firewall */}
          <div className={`ao-state${st.rule ? ' on' : ''}`}>
            <span className="ao-dot" aria-hidden="true" />
            <div>
              <b>{st.rule ? 'Windows Firewall allows it' : 'Blocked by Windows Firewall'}</b>
              <span>{st.rule
                ? 'Other devices here may connect.'
                : 'Windows is dropping connections from your other devices.'}</span>
            </div>
          </div>
          {!st.rule && (
            <button className="btn block" disabled={busy}
              onClick={() => run('/api/hosting/local-network', 'Firewall rule added')}>
              {busy ? 'Asking Windows…' : 'Allow through the firewall'}
            </button>
          )}

          {/* Network category. A Public network ignores the rule above entirely,
              so a copy can look correctly configured and still be unreachable. */}
          <div className={`ao-state${st.private_network ? ' on' : ''}`} style={{ marginTop: 12 }}>
            <span className="ao-dot" aria-hidden="true" />
            <div>
              <b>{st.private_network
                ? 'This Wi-Fi is treated as a home network'
                : `“${netName}” is set to Public`}</b>
              <span>{st.private_network
                ? 'Devices here are allowed to find this computer.'
                : 'On a Public network Windows hides this computer from your own devices, even with the firewall rule above.'}</span>
            </div>
          </div>
          {!st.private_network && (
            <button className="btn ghost block" style={{ marginTop: 8 }} disabled={busy}
              onClick={() => run('/api/hosting/local-network/private', 'Network changed')}>
              {busy ? 'Asking Windows…' : 'Treat this Wi-Fi as my home network'}
            </button>
          )}

          {st.allowed && st.address && (
            <p className="form-hint" style={{ marginTop: 12, marginBottom: 0 }}>
              Open <code>{st.address}</code> on your phone, while it is on the same Wi-Fi.
            </p>
          )}
          {!st.allowed && (
            <p className="form-hint" style={{ marginTop: 12, marginBottom: 0 }}>
              Windows will ask for permission. Only do this on a network you trust —
              your own home or office, not public Wi-Fi.
            </p>
          )}
        </div>
      </SettingsBlock>
    </SettingsGroup>
  )
}

function AlwaysOnSection({ onOpenWeb }: { onOpenWeb: () => void }) {
  const toast = useToast()
  const [st, setSt] = useState<AlwaysOn | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<AlwaysOn>('/api/hosting/always-on').then(setSt).catch(() => setSt(null))
  }, [])
  useEffect(() => { load() }, [load])

  if (!st) return null

  async function toggle() {
    setBusy(true)
    try {
      const next = st!.startup.enabled
        ? await api<AlwaysOn>('/api/hosting/always-on', { method: 'DELETE' })
        : await api<AlwaysOn>('/api/hosting/always-on', { method: 'POST' })
      setSt(next)
      toast(next.startup.enabled
        ? `${appName()} will start when this computer starts`
        : 'It will no longer start on its own')
    } catch (e) { toast(errorMessage(e, 'Could not change that')) }
    finally { setBusy(false) }
  }

  const on = st.startup.enabled
  const tunnelOk = st.tunnel.running

  return (
    <SettingsGroup title="Act as my server"
      footer={st.startup.supported
        ? `With this on, ${appName()} starts by itself whenever you switch this computer on — so your records are reachable from anywhere without you opening anything. No administrator rights needed.`
        : st.startup.reason}>

      <SettingsBlock>
        <div className="ao">
          <div className={`ao-state${on ? ' on' : ''}`}>
            <span className="ao-dot" aria-hidden="true" />
            <div>
              <b>{on ? 'Starts with this computer' : 'Only runs when you open it'}</b>
              <span>{on
                ? 'Switch the computer on and it is serving.'
                : 'Close it or restart, and your address stops answering.'}</span>
            </div>
          </div>

          <button className={on ? 'btn ghost block' : 'btn block'} disabled={busy || !st.startup.supported}
            onClick={toggle}>
            {busy ? 'Working…' : on ? 'Stop starting automatically' : 'Start with my computer'}
          </button>

          {/* The second half of the promise. Starting at login is no use if the
              connector is not running, so say which piece is missing. */}
          <div className={`ao-state${tunnelOk ? ' on' : ''}`} style={{ marginTop: 12 }}>
            <span className="ao-dot" aria-hidden="true" />
            <div>
              <b>{tunnelOk ? 'Reachable from anywhere' : 'On this network only'}</b>
              <span>{tunnelOk
                ? 'The connection to your web address is up.'
                : st.tunnel.installed
                  ? 'No web address set up yet.'
                  : 'The Cloudflare connector is not installed on this computer.'}</span>
            </div>
          </div>
          {!tunnelOk && (
            <button className="btn ghost block" style={{ marginTop: 8 }} onClick={onOpenWeb}>
              Set up my web address
            </button>
          )}

          {on && st.startup.path && (
            <p className="form-hint" style={{ marginBottom: 0 }}>
              Recorded in <code>{st.startup.path}</code> — you can delete that file
              yourself at any time.
            </p>
          )}
        </div>
      </SettingsBlock>
    </SettingsGroup>
  )
}
