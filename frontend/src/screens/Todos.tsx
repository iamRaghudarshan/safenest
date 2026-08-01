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
  const { items, loading, reload, refresh, create, remove, error} = useResource<Todo>('/api/todos')
  const toast = useToast()
  const { refresh: refreshAttn } = useAttention()
  const [open, setOpen] = useState(false)

  async function toggle(id: number) { await api(`/api/todos/${id}/toggle`, { method: 'POST' }); reload(); refreshAttn() }
  async function save(body: Partial<Todo>) { await create(body); refreshAttn(); toast('Task added'); setOpen(false) }

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
        {pendingSorted.map((t) => <TodoRow key={t.id} t={t} onToggle={() => toggle(t.id)} onDelete={() => { remove(t.id); toast('Deleted') }} />)}
      </div>
      {done.length > 0 && <>
        <div className="section-title">Done</div>
        <div className="list" style={{ gap: 8 }}>
          {done.map((t) => <TodoRow key={t.id} t={t} onToggle={() => toggle(t.id)} onDelete={() => { remove(t.id); toast('Deleted') }} />)}
        </div>
      </>}
      {open && <TodoForm onSave={save} onClose={() => setOpen(false)} />}
    </ModuleScreen>
  )
}

function TodoRow({ t, onToggle, onDelete }: { t: Todo; onToggle: () => void; onDelete: () => void }) {
  const done = t.status === 'done'
  const p = PRIO[t.priority] || PRIO.medium
  return (
    <SwipeRow onSwipeRight={onToggle} onSwipeLeft={onDelete} rightLabel={done ? 'Undo' : 'Done'}>
      <div className={`task${done ? ' done' : ''}`}>
        <button className="task-check sq" onClick={onToggle}
          style={done ? { background: p.c, borderColor: p.c } : { borderColor: p.c }}>{done ? '✓' : ''}</button>
        <div className="task-main">
          <div className={`task-title${done ? ' struck' : ''}`}>{t.title}</div>
          {t.due_date && <div className="task-sub">{fmtDate(t.due_date)}</div>}
        </div>
        {!done && <span className="pill" style={{ background: `color-mix(in srgb, ${p.c} 15%, transparent)`, color: p.c }}>{p.l}</span>}
        <button onClick={onDelete} className="task-del" aria-label="Delete">×</button>
      </div>
    </SwipeRow>
  )
}

function TodoForm({ onSave, onClose }: { onSave: (b: Partial<Todo>) => void; onClose: () => void }) {
  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState<Todo['priority']>('medium')
  const [due_date, setDue] = useState('')
  return (
    <Sheet title="New task" onClose={onClose}>
      <Field label="Task"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Call the accountant" autoFocus /></Field>
      <Field label="Priority">
        <Segment value={priority} onChange={setPriority}
          options={[{ value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High' }]} />
      </Field>
      <Field label="Due date (optional)"><input className="input" type="date" value={due_date} onChange={(e) => setDue(e.target.value)} /></Field>
      <button className="btn block" onClick={() => onSave({ title, priority, due_date })} disabled={!title.trim()}>Add task</button>
    </Sheet>
  )
}
