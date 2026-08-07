import { useState } from 'react'
import { api } from '../api'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { fmtDate } from '../format'
import { Sheet, Field, Segment } from '../ui'
import { SwipeRow } from '../SwipeRow'
import { useAttention } from '../attention'
import { ModuleScreen } from './Scaffold'
import type { Todo } from '../types'

const PRIO: Record<string, { c: string; l: string }> = {
  high: { c: 'var(--danger)', l: 'High' }, medium: { c: 'var(--warn)', l: 'Medium' }, low: { c: 'var(--ok)', l: 'Low' },
}

export default function Todos() {
  const { items, loading, reload, refresh, create, update, remove, error} = useResource<Todo>('/api/todos')
  const toast = useToast()
  const { refresh: refreshAttn } = useAttention()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Todo | null>(null)

  async function toggle(id: number) { await api(`/api/todos/${id}/toggle`, { method: 'POST' }); reload(); refreshAttn() }
  async function save(body: Partial<Todo>) {
    if (editing) { await update(editing.id, body); toast('Task updated') }
    else { await create(body); toast('Task added') }
    refreshAttn(); setOpen(false); setEditing(null)
  }

  const pending = items.filter((t) => t.status === 'pending')
  const done = items.filter((t) => t.status === 'done')
  // pending sorted by priority (high → low) for a sensible order
  const rank: Record<string, number> = { high: 0, medium: 1, low: 2 }
  const pendingSorted = [...pending].sort((a, b) => (rank[a.priority] ?? 1) - (rank[b.priority] ?? 1))

  return (
    <ModuleScreen mod="todo" sub={`${pending.length} to do`} loading={loading} empty={items.length === 0} onAdd={() => setOpen(true)} error={error} onRetry={reload} onRefresh={refresh}>
      {items.length > 0 && (
        <div className="card" style={{ display: 'flex', alignItems: 'center', padding: 16 }}>
          <div style={{ flex: 1 }}>
            <div className="mcard-label">To do</div>
            <div className="tabnum" style={{ fontSize: 21, fontWeight: 800 }}>{pending.length}</div>
          </div>
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)', margin: '2px 0' }} />
          <div style={{ flex: 1, paddingLeft: 16 }}>
            <div className="mcard-label">Done</div>
            <div className="tabnum" style={{ fontSize: 21, fontWeight: 800, color: 'var(--ok)' }}>{done.length}</div>
          </div>
        </div>
      )}
      <div className="list" style={{ gap: 8, marginTop: 4 }}>
        {pendingSorted.map((t) => <TodoRow key={t.id} t={t} onToggle={() => toggle(t.id)} onEdit={() => { setEditing(t); setOpen(true) }} onDelete={() => { remove(t.id); toast('Deleted') }} />)}
      </div>
      {done.length > 0 && <>
        <div className="section-title">Done</div>
        <div className="list" style={{ gap: 8 }}>
          {done.map((t) => <TodoRow key={t.id} t={t} onToggle={() => toggle(t.id)} onEdit={() => { setEditing(t); setOpen(true) }} onDelete={() => { remove(t.id); toast('Deleted') }} />)}
        </div>
      </>}
      {open && <TodoForm edit={editing} onSave={save} onClose={() => { setOpen(false); setEditing(null) }} />}
    </ModuleScreen>
  )
}

function TodoRow({ t, onToggle, onEdit, onDelete }: { t: Todo; onToggle: () => void; onEdit: () => void; onDelete: () => void }) {
  const done = t.status === 'done'
  const p = PRIO[t.priority] || PRIO.medium
  return (
    <SwipeRow onSwipeRight={onToggle} onSwipeLeft={onDelete} rightLabel={done ? 'Undo' : 'Done'}>
      <div className={`task${done ? ' done' : ''}`}>
        <button className="task-check sq" onClick={onToggle}
          style={done ? { background: p.c, borderColor: p.c } : { borderColor: p.c }}>{done ? '✓' : ''}</button>
        <div className="task-main" onClick={onEdit} style={{ cursor: 'pointer' }}>
          <div className={`task-title${done ? ' struck' : ''}`}>{t.title}</div>
          {(t.due_date || (t.recurrence && t.recurrence !== 'none')) && (
            <div className="task-sub">
              {t.due_date ? fmtDate(t.due_date) : ''}
              {t.due_date && t.recurrence && t.recurrence !== 'none' ? ' · ' : ''}
              {t.recurrence && t.recurrence !== 'none' ? t.recurrence : ''}
            </div>
          )}
        </div>
        {!done && <span className="pill" style={{ background: `color-mix(in srgb, ${p.c} 15%, transparent)`, color: p.c }}>{p.l}</span>}
        <button onClick={onDelete} className="task-del" aria-label="Delete">×</button>
      </div>
    </SwipeRow>
  )
}

function TodoForm({ edit, onSave, onClose }: { edit?: Todo | null; onSave: (b: Partial<Todo>) => void; onClose: () => void }) {
  const [title, setTitle] = useState(edit?.title || '')
  const [priority, setPriority] = useState<Todo['priority']>(edit?.priority || 'medium')
  const [due_date, setDue] = useState(edit?.due_date || '')
  const [recurrence, setRec] = useState(edit?.recurrence || 'none')
  return (
    <Sheet title={edit ? 'Edit task' : 'New task'} onClose={onClose}>
      <Field label="Task"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Call the accountant" autoFocus /></Field>
      <Field label="Priority">
        <Segment value={priority} onChange={setPriority}
          options={[{ value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' }]} />
      </Field>
      <div className="row2">
        <Field label="Due date (optional)"><input className="input" type="date" value={due_date} onChange={(e) => setDue(e.target.value)} /></Field>
        {/* The column and its default have always existed; nothing ever sent a
            value, so a task that comes back every week could not be written down.
            No "yearly" here — unlike reminders, this column does not allow it. */}
        <Field label="Repeat"><select className="select" value={recurrence} onChange={(e) => setRec(e.target.value)}><option value="none">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></Field>
      </div>
      <button className="btn block" onClick={() => onSave({ title, priority, due_date, recurrence })} disabled={!title.trim()}>{edit ? 'Save changes' : 'Add task'}</button>
    </Sheet>
  )
}
