import { useEffect, useState } from 'react'
import { api } from '../api'
import { useNav } from '../nav'
import { useToast } from '../toast'
import { TopBar, Sheet, Field, Segment, Spinner } from '../ui'
import { MODULES } from '../modules'
import type { AdminUser, ModuleKey } from '../types'

type Matrix = Record<string, { view: boolean; create: boolean; edit: boolean; delete: boolean }>

const MIN_PASSWORD_LEN = 12 // must match backend security.MIN_PASSWORD_LEN

export default function Admin() {
  const { back } = useNav()
  const toast = useToast()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [edit, setEdit] = useState<AdminUser | null | 'new'>(null)
  const [perms, setPerms] = useState<AdminUser | null>(null)
  const [confirmDel, setConfirmDel] = useState<AdminUser | null>(null)

  async function load() {
    setLoading(true)
    const d = await api<{ users: AdminUser[] }>('/api/admin/users')
    setUsers(d.users); setLoading(false)
  }
  useEffect(() => { load() }, [])

  async function save(body: Record<string, unknown>) {
    try {
      if (edit === 'new') { await api('/api/admin/users', { method: 'POST', body }); toast('User created') }
      else if (edit) { await api(`/api/admin/users/${edit.id}`, { method: 'PUT', body }); toast('User updated') }
      setEdit(null); load()
    } catch (e) { toast(e instanceof Error ? e.message : 'Failed') }
  }

  async function del(u: AdminUser) {
    try {
      await api(`/api/admin/users/${u.id}`, { method: 'DELETE' })
      toast('Account and all its data deleted'); setConfirmDel(null); load()
    } catch (e) { toast(e instanceof Error ? e.message : 'Failed') }
  }

  return (
    <div className="screen">
      <TopBar title="Users" sub={`${users.length} accounts`} onBack={back}
        right={<button className="btn sm" onClick={() => setEdit('new')}>+ New</button>} />
      {loading ? <Spinner /> : (
        <div className="list">
          {users.map((u) => (
            <div key={u.id} className="card" style={{ padding: 14 }}>
              <div className="rowitem">
                <div className="avatar" style={{ background: u.role === 'admin' ? 'linear-gradient(135deg,#334155,#0f172a)' : undefined }}>{u.initials}</div>
                <div className="main">
                  <div className="t">{u.name} {u.status === 'suspended' && <span className="pill danger" style={{ marginLeft: 4 }}>Suspended</span>}</div>
                  <div className="s">{u.email} · {u.role === 'admin' ? 'All access' : `${u.modules_granted} modules`}</div>
                </div>
              </div>
              <div className="swipe-actions">
                <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => setEdit(u)}>Edit</button>
                {u.role !== 'admin' && <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => setPerms(u)}>Permissions</button>}
                <button className="btn danger sm" style={{ flex: 1 }} onClick={() => setConfirmDel(u)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {edit && <UserForm initial={edit === 'new' ? null : edit} onSave={save} onClose={() => setEdit(null)} />}
      {perms && <PermSheet user={perms} onClose={() => setPerms(null)} onChange={load} />}
      {confirmDel && <DeleteUser user={confirmDel} onClose={() => setConfirmDel(null)} onConfirm={() => del(confirmDel)} />}
    </div>
  )
}

interface UserData {
  counts: Record<string, number>
  totalRows: number
  files: number
  bytes: number
}

const mb = (n: number) => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`

// Deleting an account now erases everything it owned, so show exactly what that
// means and require the email to be typed — this is not undoable.
function DeleteUser({ user, onClose, onConfirm }: { user: AdminUser; onClose: () => void; onConfirm: () => void }) {
  const [data, setData] = useState<UserData | null>(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api<UserData>(`/api/admin/users/${user.id}/data`).then(setData)
      .catch(() => setData({ counts: {}, totalRows: 0, files: 0, bytes: 0 }))
  }, [user.id])

  const entries = Object.entries(data?.counts || {})
  return (
    <Sheet title="Delete account?" onClose={onClose}>
      <div style={{ textAlign: 'center', fontSize: 38, marginBottom: 6 }}>⚠️</div>
      <p className="muted" style={{ fontSize: 13.5, marginBottom: 14, textAlign: 'center' }}>
        This permanently erases <b>{user.name}</b> ({user.email}) and everything the account owns.
        This <b>can’t be undone</b>.
      </p>

      {!data ? <Spinner /> : (
        <div className="card" style={{ padding: 12, marginBottom: 14 }}>
          {entries.length === 0 && <div className="muted" style={{ fontSize: 13 }}>No stored data.</div>}
          {entries.map(([label, n]) => (
            <div key={label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '3px 0' }}>
              <span className="muted" style={{ textTransform: 'capitalize' }}>{label}</span>
              <b className="tabnum">{n.toLocaleString()}</b>
            </div>
          ))}
          {data.files > 0 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '3px 0', borderTop: '1px solid var(--line)', marginTop: 6, paddingTop: 8 }}>
              <span className="muted">Stored files</span>
              <b className="tabnum">{data.files.toLocaleString()} · {mb(data.bytes)}</b>
            </div>
          )}
        </div>
      )}

      <Field label={`Type “${user.email}” to confirm`}>
        <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={user.email} autoComplete="off" />
      </Field>
      <div style={{ display: 'flex', gap: 10 }}>
        <button className="btn ghost block" onClick={onClose}>Cancel</button>
        <button className="btn danger block" disabled={typed.trim().toLowerCase() !== user.email.toLowerCase() || busy}
          onClick={() => { setBusy(true); onConfirm() }}>{busy ? 'Deleting…' : 'Delete permanently'}</button>
      </div>
    </Sheet>
  )
}

function UserForm({ initial, onSave, onClose }: { initial: AdminUser | null; onSave: (b: Record<string, unknown>) => void; onClose: () => void }) {
  const [f, setF] = useState<Record<string, string>>({ name: initial?.name || '', email: initial?.email || '', role: initial?.role || 'user', status: initial?.status || 'active', password: '' })
  const [showPw, setShowPw] = useState(false)
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }))

  // Optional per-module access chosen AT creation. Defaults to full access, so a
  // plain create behaves exactly as before; tick "Choose access" to restrict.
  const allKeys = Object.keys(MODULES) as ModuleKey[]
  const [customPerms, setCustomPerms] = useState(false)
  const [perms, setPerms] = useState<Matrix>(() =>
    Object.fromEntries(allKeys.map((k) => [k, { view: true, create: true, edit: true, delete: true }])) as Matrix)
  const togglePerm = (m: string, a: string, v: boolean) =>
    setPerms((p) => ({ ...p, [m]: { ...p[m], [a]: v } }))

  // One place decides both the message and whether the button is usable, so they
  // can never disagree — the reason is always visible when the button is blocked.
  const pw = f.password
  const problem =
    !f.name.trim() ? 'Enter the person’s full name'
      : !/^\S+@\S+\.\S+$/.test(f.email.trim()) ? 'Enter a valid email address'
        : (!initial && pw.length === 0) ? `Set a password of at least ${MIN_PASSWORD_LEN} characters`
          : (pw.length > 0 && pw.length < MIN_PASSWORD_LEN)
            ? `Password needs ${MIN_PASSWORD_LEN - pw.length} more character${MIN_PASSWORD_LEN - pw.length === 1 ? '' : 's'}`
            : (pw.length > 0 && new Set(pw).size < 5) ? 'Password is too repetitive — mix in more characters'
              : ''
  return (
    <Sheet title={initial ? 'Edit user' : 'New user'} onClose={onClose}>
      <Field label="Full name"><input className="input" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
      <Field label="Email"><input className="input" type="email" value={f.email} onChange={(e) => set('email', e.target.value)} /></Field>
      <div className="row2">
        <Field label="Role"><Segment value={f.role} onChange={(v) => set('role', v)} options={[{ value: 'user', label: 'User' }, { value: 'admin', label: 'Admin' }]} /></Field>
        <Field label="Status"><Segment value={f.status} onChange={(v) => set('status', v)} options={[{ value: 'active', label: 'Active' }, { value: 'suspended', label: 'Suspend' }]} /></Field>
      </div>
      <Field label={initial ? 'New password (optional)' : 'Password'}>
        <div className="pw-wrap">
          <input className="input" type={showPw ? 'text' : 'password'} autoComplete="new-password"
            value={f.password} onChange={(e) => set('password', e.target.value)}
            placeholder={initial ? 'Leave blank to keep current' : `At least ${MIN_PASSWORD_LEN} characters`} />
          <button type="button" className="pw-toggle" onClick={() => setShowPw((s) => !s)}>
            {showPw ? 'Hide' : 'Show'}
          </button>
        </div>
      </Field>

      {/* Choose access at creation. Only offered for a new non-admin user;
          admins get everything, and edits use the dedicated Permissions sheet. */}
      {!initial && f.role === 'user' && (
        <Field label="Access">
          <label className="perm-all">
            <input type="checkbox" checked={!customPerms} onChange={(e) => setCustomPerms(!e.target.checked)} />
            <span>All modules (full access)</span>
          </label>
          {customPerms && (
            <div className="list" style={{ marginTop: 8 }}>
              {allKeys.map((k) => {
                const row = perms[k]; const Icon = MODULES[k].Icon
                return (
                  <div key={k} className="card" style={{ padding: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <div style={{ width: 26, height: 26, borderRadius: 8, display: 'grid', placeItems: 'center', color: '#fff', background: MODULES[k].color }}><Icon className="ic" /></div>
                      <b style={{ fontSize: 13 }}>{MODULES[k].label}</b>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 6 }}>
                      {(['view', 'create', 'edit', 'delete'] as const).map((a) => (
                        <button key={a} type="button" onClick={() => togglePerm(k, a, !row[a])}
                          className="pill" style={{ justifyContent: 'center', padding: '7px 0', textTransform: 'capitalize', background: row[a] ? 'var(--brand)' : 'var(--bg)', color: row[a] ? '#fff' : 'var(--ink-soft)', border: '1.5px solid var(--line)' }}>
                          {a}
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Field>
      )}

      {/* Say WHY the button is disabled. A silently dead button reads as a broken app. */}
      {problem && <p className="form-hint warn">{problem}</p>}
      {initial && f.password.length > 0 && !problem && (
        <p className="form-hint warn">This signs {f.name.trim() || 'the user'} out on every device</p>
      )}
      {!initial && !problem && f.role === 'user' && !customPerms && (
        <p className="form-hint">They’ll get access to all modules; fine-tune with Permissions afterwards.</p>
      )}
      {!initial && !problem && f.role === 'user' && customPerms && (
        <p className="form-hint">Only the ticked actions will be allowed. You can change these later.</p>
      )}

      <button className="btn block"
        onClick={() => onSave(!initial && f.role === 'user' && customPerms ? { ...f, permissions: perms } : f)}
        disabled={!!problem}>
        {initial ? 'Save changes' : 'Create user'}
      </button>
    </Sheet>
  )
}

function PermSheet({ user, onClose, onChange }: { user: AdminUser; onClose: () => void; onChange: () => void }) {
  const toast = useToast()
  const [matrix, setMatrix] = useState<Matrix | null>(null)
  useEffect(() => { api<{ matrix: Matrix }>(`/api/admin/users/${user.id}/permissions`).then((d) => setMatrix(d.matrix)) }, [user.id])

  async function toggle(module: string, action: string, value: boolean) {
    setMatrix((m) => m ? { ...m, [module]: { ...m[module], [action]: value } } : m)
    await api('/api/admin/permissions', { method: 'POST', body: { userId: user.id, module, action, value } })
    onChange()
  }

  return (
    <Sheet title={`${user.name} · permissions`} onClose={onClose}>
      {!matrix ? <Spinner /> : (
        <div className="list">
          {(Object.keys(MODULES) as ModuleKey[]).map((k) => {
            const row = matrix[k] || { view: false, create: false, edit: false, delete: false }
            const Icon = MODULES[k].Icon
            return (
              <div key={k} className="card" style={{ padding: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                  <div style={{ width: 30, height: 30, borderRadius: 9, display: 'grid', placeItems: 'center', color: '#fff', background: MODULES[k].color }}><Icon className="ic" /></div>
                  <b style={{ fontSize: 14 }}>{MODULES[k].label}</b>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 6 }}>
                  {(['view', 'create', 'edit', 'delete'] as const).map((a) => (
                    <button key={a} onClick={() => toggle(k, a, !row[a])}
                      className="pill" style={{ justifyContent: 'center', padding: '8px 0', textTransform: 'capitalize', background: row[a] ? 'var(--brand)' : 'var(--bg)', color: row[a] ? '#fff' : 'var(--ink-soft)', border: '1.5px solid var(--line)' }}>
                      {a}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
          <button className="btn block" onClick={() => { toast('Permissions saved'); onClose() }}>Done</button>
        </div>
      )}
    </Sheet>
  )
}
