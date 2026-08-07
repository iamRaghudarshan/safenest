import { useEffect, useState } from 'react'
import { api } from '../api'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { useNav } from '../nav'
import { useAttention } from '../attention'
import { dueLabel } from '../format'
import { Sheet, Field } from '../ui'
import { SwipeRow } from '../SwipeRow'
import { ModuleScreen } from './Scaffold'
import { MODULES } from '../modules'
import type { Reminder, ModuleKey } from '../types'

const MOD_OPTS: (ModuleKey | 'general')[] = ['general', 'loans', 'cards', 'insurance', 'investments', 'expenses', 'todo']

export default function Reminders() {
  const { items, loading, reload, refresh, create, update, remove, error} = useResource<Reminder>('/api/reminders')
  const toast = useToast()
  const { takeIntent } = useNav()
  const { refresh: refreshAttn } = useAttention()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Reminder | null>(null)

  useEffect(() => { if (takeIntent() === 'add') setOpen(true) }, [takeIntent])

  async function toggle(id: number) { await api(`/api/reminders/${id}/toggle`, { method: 'POST' }); reload(); refreshAttn() }

  // The PUT has always been there; nothing on this screen ever called it, so a
  // reminder with a typo or a date that moved could only be deleted and retyped.
  async function save(body: Partial<Reminder>) {
    if (editing) { await update(editing.id, body); toast('Reminder updated') }
    else { await create(body); toast('Reminder set') }
    refreshAttn(); setOpen(false); setEditing(null)
  }

  const pending = items.filter((r) => !r.is_done)
  const done = items.filter((r) => r.is_done)
  const dueSoon = pending.filter((r) => r.days != null && r.days <= 7).length

  // group pending by module
  const groups: Record<string, Reminder[]> = {}
  pending.forEach((r) => { const k = r.module_ref || 'general'; (groups[k] ||= []).push(r) })

  return (
    <ModuleScreen mod="reminders" sub={`${pending.length} pending`} loading={loading} empty={items.length === 0} onAdd={() => setOpen(true)} error={error} onRetry={reload} onRefresh={refresh}>
      {pending.length > 0 && (
        <div className="card" style={{ display: 'flex', alignItems: 'center', padding: 16 }}>
          <div style={{ flex: 1 }}>
            <div className="mcard-label">Pending</div>
            <div className="tabnum" style={{ fontSize: 21, fontWeight: 800 }}>{pending.length}</div>
          </div>
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)', margin: '2px 0' }} />
          <div style={{ flex: 1, paddingLeft: 16 }}>
            <div className="mcard-label">Due this week</div>
            <div className="tabnum" style={{ fontSize: 21, fontWeight: 800, color: dueSoon ? 'var(--warn)' : 'var(--ink)' }}>{dueSoon}</div>
          </div>
        </div>
      )}
      {Object.entries(groups).map(([mod, rows]) => (
        <div key={mod}>
          <div className="section-title" style={{ marginTop: 12 }}>{mod === 'general' ? 'General' : MODULES[mod as ModuleKey]?.label || mod}</div>
          <div className="list" style={{ gap: 8 }}>
            {rows.map((r) => <ReminderRow key={r.id} r={r} onToggle={() => toggle(r.id)} onEdit={() => { setEditing(r); setOpen(true) }} onDelete={() => { remove(r.id); toast('Deleted') }} />)}
          </div>
        </div>
      ))}
      {done.length > 0 && <>
        <div className="section-title">Completed</div>
        <div className="list" style={{ gap: 8 }}>
          {done.map((r) => <ReminderRow key={r.id} r={r} onToggle={() => toggle(r.id)} onEdit={() => { setEditing(r); setOpen(true) }} onDelete={() => { remove(r.id); toast('Deleted') }} />)}
        </div>
      </>}
      {open && <ReminderForm edit={editing} onSave={save} onClose={() => { setOpen(false); setEditing(null) }} />}
    </ModuleScreen>
  )
}

function ReminderRow({ r, onToggle, onEdit, onDelete }: { r: Reminder; onToggle: () => void; onEdit: () => void; onDelete: () => void }) {
  const dl = dueLabel(r.days)
  const done = !!r.is_done
  const accent = r.module_ref ? MODULES[r.module_ref as ModuleKey]?.color || 'var(--c-reminders)' : 'var(--c-reminders)'
  return (
    <SwipeRow onSwipeRight={onToggle} onSwipeLeft={onDelete} rightLabel={done ? 'Undo' : 'Done'}>
      <div className={`task${done ? ' done' : ''}`}>
        <button className="task-check" onClick={onToggle}
          style={done ? { background: accent, borderColor: accent } : { borderColor: accent }}>{done ? '✓' : ''}</button>
        {/* The body opens the editor; the tick and the cross keep their own jobs,
            so the common action stays one tap and nothing moves. */}
        <div className="task-main" onClick={onEdit} style={{ cursor: 'pointer' }}>
          <div className={`task-title${done ? ' struck' : ''}`}>{r.title}</div>
          {r.due_fmt && <div className="task-sub">{r.due_fmt}{r.recurrence && r.recurrence !== 'none' ? ` · ${r.recurrence}` : ''}</div>}
        </div>
        {/* An hour is the thing you scan this list for, so it gets its own mark
            rather than being buried at the end of the date line. */}
        {!done && r.time_fmt && <span className="pill">🕑 {r.time_fmt}</span>}
        {!done && r.days != null && <span className={`pill ${dl.tone}`}>{dl.text}</span>}
        <button onClick={onDelete} className="task-del" aria-label="Delete">×</button>
      </div>
    </SwipeRow>
  )
}

function ReminderForm({ edit, onSave, onClose }: { edit?: Reminder | null; onSave: (b: Partial<Reminder>) => void; onClose: () => void }) {
  const [title, setTitle] = useState(edit?.title || '')
  const [module_ref, setMod] = useState<string>(edit?.module_ref || 'general')
  const [due_date, setDue] = useState(edit?.due_date || '')
  const [due_time, setTime] = useState(edit?.due_time || '')
  const [recurrence, setRec] = useState(edit?.recurrence || 'none')

  return (
    <Sheet title={edit ? 'Edit reminder' : 'New reminder'} onClose={onClose}>
      <Field label="Title"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Pay electricity bill" autoFocus /></Field>
      <div className="row2">
        <Field label="Module"><select className="select" value={module_ref} onChange={(e) => setMod(e.target.value)}>{MOD_OPTS.map((m) => <option key={m} value={m}>{m === 'general' ? 'General' : MODULES[m as ModuleKey]?.label || m}</option>)}</select></Field>
        {/* Daily and weekly have been valid in the column since the beginning and
            were simply never offered, so a reminder to take something every
            morning could not be expressed. */}
        <Field label="Repeat"><select className="select" value={recurrence} onChange={(e) => setRec(e.target.value)}><option value="none">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="yearly">Yearly</option></select></Field>
      </div>
      <div className="row2">
        <Field label="Due date"><input className="input" type="date" value={due_date} onChange={(e) => setDue(e.target.value)} /></Field>
        {/* Optional on purpose. A reminder with no time arrives with the daily
            summary, which is how every reminder worked until now — filling this
            in is what asks to be told at a particular hour instead. */}
        <Field label="Time (optional)"><input className="input" type="time" value={due_time} onChange={(e) => setTime(e.target.value)} /></Field>
      </div>
      {due_time && !due_date && <div className="hint" style={{ color: 'var(--warn)' }}>Pick a date too — an hour on its own has no day to fall on.</div>}
      <button className="btn block"
        onClick={() => onSave({ title, module_ref: module_ref === 'general' ? null : module_ref, due_date, due_time, recurrence })}
        disabled={!title.trim() || (!!due_time && !due_date)}>{edit ? 'Save changes' : 'Set reminder'}</button>
    </Sheet>
  )
}
