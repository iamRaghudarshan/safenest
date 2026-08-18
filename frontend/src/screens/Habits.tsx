import { useEffect, useState } from 'react'
import { api } from '../api'
import { useResource } from '../useResource'
import { useToast } from '../toast'
import { Sheet, Field, Segment } from '../ui'
import { useAttention } from '../attention'
import { ModuleScreen } from './Scaffold'
import { IcEdit, IcTrash } from '../icons'
import type { Habit, HabitDay } from '../types'

// A small palette and emoji set so a habit can be made distinct at a glance,
// without shipping a picker library. Colours are the same module tints used
// everywhere else, so a habit sits visually alongside the rest of the app.
const COLORS = ['var(--c-habits)', 'var(--c-todo)', 'var(--c-reminders)', 'var(--c-insurance)',
  'var(--c-investments)', 'var(--c-cards)', 'var(--c-loans)', 'var(--c-expenses)']
const EMOJIS = ['💧', '🏃', '📚', '🧘', '💪', '🥗', '😴', '🚭', '☕', '🎯', '✍️', '🎸', '🦷', '🌅']
const WEEKDAYS: { n: number; l: string }[] = [
  { n: 1, l: 'M' }, { n: 2, l: 'T' }, { n: 3, l: 'W' }, { n: 4, l: 'T' },
  { n: 5, l: 'F' }, { n: 6, l: 'S' }, { n: 7, l: 'S' },
]

const dim = (c: string, pct: number) => `color-mix(in srgb, ${c} ${pct}%, transparent)`

export default function Habits() {
  const { items, loading, reload, refresh, create, update, remove, error } = useResource<Habit>('/api/habits')
  const toast = useToast()
  const { refresh: refreshAttn } = useAttention()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Habit | null>(null)
  const [detail, setDetail] = useState<Habit | null>(null)

  async function check(h: Habit, count?: number) {
    await api(`/api/habits/${h.id}/check`, { method: 'POST', body: count === undefined ? {} : { count } })
    reload(); refreshAttn()
  }
  async function save(body: Partial<Habit>) {
    if (editing) { await update(editing.id, body); toast('Habit updated') }
    else { await create(body); toast('Habit added') }
    refreshAttn(); setOpen(false); setEditing(null)
  }
  async function archive(h: Habit) {
    await api(`/api/habits/${h.id}/archive`, { method: 'POST' }); reload()
    toast(h.archived ? 'Un-archived' : 'Archived')
  }

  const doneToday = items.filter((h) => h.done_today).length
  const activeToday = items.filter((h) => h.active_today).length

  return (
    <ModuleScreen mod="habits" sub={`${doneToday}/${activeToday} done today`} loading={loading}
      empty={items.length === 0} onAdd={() => { setEditing(null); setOpen(true) }}
      error={error} onRetry={reload} onRefresh={refresh}>
      {items.length > 0 && (
        <div className="card" style={{ display: 'flex', alignItems: 'center', padding: 16 }}>
          <div style={{ flex: 1 }}>
            <div className="mcard-label">Done today</div>
            <div className="tabnum" style={{ fontSize: 21, fontWeight: 800, color: 'var(--ok)' }}>{doneToday}</div>
          </div>
          <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--line)', margin: '2px 0' }} />
          <div style={{ flex: 1, paddingLeft: 16 }}>
            <div className="mcard-label">Due today</div>
            <div className="tabnum" style={{ fontSize: 21, fontWeight: 800 }}>{activeToday}</div>
          </div>
        </div>
      )}
      <div className="list" style={{ gap: 8, marginTop: 4 }}>
        {items.map((h) => (
          <HabitCard key={h.id} h={h} onCheck={check} onOpen={() => setDetail(h)}
            onEdit={() => { setEditing(h); setOpen(true) }}
            onArchive={() => archive(h)}
            onDelete={() => { remove(h.id); toast('Deleted') }} />
        ))}
      </div>
      {open && <HabitForm edit={editing} onSave={save} onClose={() => { setOpen(false); setEditing(null) }} />}
      {detail && <HabitDetail h={detail} onClose={() => setDetail(null)} />}
    </ModuleScreen>
  )
}

/** One habit: a big check control, the name with today's progress, the streak,
 *  and a seven-day strip. Tapping the body opens the calendar. */
function HabitCard({ h, onCheck, onOpen, onEdit, onArchive, onDelete }: {
  h: Habit; onCheck: (h: Habit, count?: number) => void; onOpen: () => void
  onEdit: () => void; onArchive: () => void; onDelete: () => void
}) {
  const [menu, setMenu] = useState(false)
  const measured = h.target > 1 && !!h.unit
  const done = h.done_today
  const c = h.color || 'var(--c-habits)'

  // A plain habit toggles; a measured one steps up to its target then unticks.
  function tapCircle() {
    if (measured) onCheck(h, done ? 0 : Math.min(h.today_count + 1, h.target))
    else onCheck(h, done ? 0 : undefined)
  }

  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button onClick={tapCircle} aria-label={done ? 'Undo today' : 'Mark done today'}
          style={{
            width: 46, height: 46, borderRadius: '50%', flexShrink: 0, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
            border: `2px solid ${c}`, background: done ? c : dim(c, 12),
            color: done ? '#fff' : 'inherit', transition: 'all .15s',
          }}>
          {done ? '✓' : (h.icon || '◎')}
        </button>

        <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={onOpen}>
          <div className="t" style={{ fontWeight: 700 }}>
            {h.icon && !done ? '' : ''}{h.name}
          </div>
          <div className="s" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {h.current_streak > 0 && (
              <span style={{ color: c, fontWeight: 700 }}>🔥 {h.current_streak}</span>
            )}
            <span>{measured
              ? `${h.today_count}/${h.target} ${h.unit} today`
              : (done ? 'Done today' : (h.active_today ? 'Not yet today' : 'Rest day'))}</span>
          </div>
        </div>

        <WeekStrip week={h.week} color={c} />

        <button onClick={() => setMenu((m) => !m)} className="task-del" aria-label="More"
          style={{ fontSize: 18 }}>⋯</button>
      </div>

      {measured && h.active_today && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, paddingLeft: 58 }}>
          <button className="btn ghost sm" onClick={() => onCheck(h, Math.max(0, h.today_count - 1))}
            disabled={h.today_count <= 0} style={{ width: 40 }}>−</button>
          <div style={{ flex: 1, height: 8, borderRadius: 6, background: dim(c, 15), overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${Math.min(100, (h.today_count / h.target) * 100)}%`, background: c }} />
          </div>
          <button className="btn ghost sm" onClick={() => onCheck(h, h.today_count + 1)} style={{ width: 40 }}>＋</button>
        </div>
      )}

      {menu && (
        <div className="swipe-actions">
          <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => { setMenu(false); onEdit() }}><IcEdit className="ic" /> Edit</button>
          <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => { setMenu(false); onArchive() }}>{h.archived ? 'Un-archive' : 'Archive'}</button>
          <button className="btn danger sm" style={{ flex: 1 }} onClick={() => { setMenu(false); onDelete() }}><IcTrash className="ic" /> Delete</button>
        </div>
      )}
    </div>
  )
}

/** The last seven days as dots: filled when done, a ring when the goal applied
 *  but was missed, faint when it was a rest day. The last dot is today. */
function WeekStrip({ week, color }: { week: HabitDay[]; color: string }) {
  return (
    <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
      {week.map((d, i) => {
        const today = i === week.length - 1
        const style: React.CSSProperties = {
          width: 9, height: 9, borderRadius: '50%',
          border: today ? `1.5px solid ${color}` : '1.5px solid transparent',
        }
        if (d.done) style.background = color
        else if (d.active) { style.background = dim(color, 18); style.border = `1.5px solid ${dim(color, 40)}` }
        else style.background = 'var(--line)'
        return <div key={d.date} style={style} title={d.date} />
      })}
    </div>
  )
}

function HabitForm({ edit, onSave, onClose }: {
  edit?: Habit | null; onSave: (b: Partial<Habit>) => void; onClose: () => void
}) {
  const [name, setName] = useState(edit?.name || '')
  const [icon, setIcon] = useState(edit?.icon || '🎯')
  const [color, setColor] = useState(edit?.color || COLORS[0])
  const [kind, setKind] = useState<Habit['kind']>(edit?.kind || 'build')
  const [goal, setGoal] = useState<Habit['goal_type']>(edit?.goal_type || 'daily')
  const [days, setDays] = useState<number[]>(
    (edit?.weekdays || '').split(',').map((x) => parseInt(x)).filter((n) => n >= 1 && n <= 7))
  const [target, setTarget] = useState(String(edit?.target_count ?? 1))
  const [unit, setUnit] = useState(edit?.unit || '')
  const [weekly, setWeekly] = useState(String(edit?.weekly_target ?? 3))
  const [time, setTime] = useState(edit?.reminder_time || '')
  const [note, setNote] = useState(edit?.note || '')

  function toggleDay(n: number) {
    setDays((d) => d.includes(n) ? d.filter((x) => x !== n) : [...d, n].sort())
  }
  function submit() {
    onSave({
      name, icon, color, kind, goal_type: goal,
      weekdays: goal === 'weekdays' ? days.join(',') : '',
      target_count: Math.max(1, parseInt(target) || 1),
      unit: (parseInt(target) || 1) > 1 ? unit.trim() : '',
      weekly_target: Math.max(1, parseInt(weekly) || 1),
      reminder_time: time || '',
      note: note.trim(),
    })
  }

  return (
    <Sheet title={edit ? 'Edit habit' : 'New habit'} onClose={onClose}>
      <Field label="Habit">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Drink water" autoFocus />
      </Field>

      <Field label="Icon">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {EMOJIS.map((e) => (
            <button key={e} type="button" onClick={() => setIcon(e)}
              style={{
                width: 40, height: 40, fontSize: 20, borderRadius: 10, cursor: 'pointer',
                border: icon === e ? '2px solid var(--brand)' : '1px solid var(--line)',
                background: icon === e ? 'color-mix(in srgb, var(--brand) 12%, transparent)' : 'var(--card)',
              }}>{e}</button>
          ))}
        </div>
      </Field>

      <Field label="Colour">
        <div style={{ display: 'flex', gap: 8 }}>
          {COLORS.map((c) => (
            <button key={c} type="button" onClick={() => setColor(c)} aria-label="colour"
              style={{
                width: 30, height: 30, borderRadius: '50%', background: c, cursor: 'pointer',
                border: color === c ? '3px solid var(--ink)' : '2px solid var(--line)',
              }} />
          ))}
        </div>
      </Field>

      <Field label="Type">
        <Segment value={kind} onChange={setKind}
          options={[{ value: 'build', label: 'Build a habit' }, { value: 'quit', label: 'Quit a habit' }]} />
      </Field>

      <Field label="How often">
        <Segment value={goal} onChange={setGoal}
          options={[{ value: 'daily', label: 'Every day' }, { value: 'weekdays', label: 'Certain days' }, { value: 'weekly', label: 'Times / week' }]} />
      </Field>

      {goal === 'weekdays' && (
        <Field label="On these days">
          <div style={{ display: 'flex', gap: 6 }}>
            {WEEKDAYS.map((d) => (
              <button key={d.n} type="button" onClick={() => toggleDay(d.n)}
                style={{
                  flex: 1, height: 40, borderRadius: 10, cursor: 'pointer', fontWeight: 700,
                  border: days.includes(d.n) ? '2px solid var(--brand)' : '1px solid var(--line)',
                  background: days.includes(d.n) ? 'color-mix(in srgb, var(--brand) 12%, transparent)' : 'var(--card)',
                }}>{d.l}</button>
            ))}
          </div>
        </Field>
      )}

      {goal === 'weekly' && (
        <Field label="Days per week">
          <input className="input" type="number" min={1} max={7} value={weekly}
            onChange={(e) => setWeekly(e.target.value)} />
        </Field>
      )}

      <div className="row2">
        <Field label="Target per day">
          <input className="input" type="number" min={1} value={target}
            onChange={(e) => setTarget(e.target.value)} />
        </Field>
        <Field label="Unit (optional)">
          <input className="input" value={unit} onChange={(e) => setUnit(e.target.value)}
            placeholder="glasses, min…" disabled={(parseInt(target) || 1) <= 1} />
        </Field>
      </div>

      <Field label="Reminder (optional)">
        <input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
      </Field>

      <Field label="Note (optional)">
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="Why this matters to you" />
      </Field>

      <button className="btn block" onClick={submit} disabled={!name.trim()
        || (goal === 'weekdays' && days.length === 0)}>
        {edit ? 'Save changes' : 'Add habit'}
      </button>
    </Sheet>
  )
}

/** The calendar: the last ~13 weeks as columns of seven days, plus the numbers
 *  that make a habit satisfying — current run, best run, and a 30-day rate. */
function HabitDetail({ h, onClose }: { h: Habit; onClose: () => void }) {
  const [days, setDays] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const c = h.color || 'var(--c-habits)'

  useEffect(() => {
    api<{ days: HabitDay[] }>(`/api/habits/${h.id}/history`)
      .then((r) => {
        const m: Record<string, boolean> = {}
        for (const d of r.days) m[d.date] = d.done
        setDays(m)
      })
      .finally(() => setLoading(false))
  }, [h.id])

  // Build 13 columns ending this week; each column is Mon…Sun.
  const today = new Date()
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  const start = new Date(today)
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7) - 12 * 7) // Monday, 12 weeks back
  const weeks: Date[][] = []
  for (let w = 0; w < 13; w++) {
    const col: Date[] = []
    for (let day = 0; day < 7; day++) {
      const d = new Date(start)
      d.setDate(start.getDate() + w * 7 + day)
      col.push(d)
    }
    weeks.push(col)
  }

  return (
    <Sheet title={`${h.icon || ''} ${h.name}`.trim()} onClose={onClose}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
        <Stat label="Current" value={`🔥 ${h.current_streak}`} color={c} />
        <Stat label="Best" value={`${h.best_streak}`} color={c} />
        <Stat label="30-day" value={`${h.rate30}%`} color={c} />
      </div>

      {loading ? <div className="spinner" /> : (
        <div style={{ display: 'flex', gap: 3, overflowX: 'auto', paddingBottom: 6 }}>
          {weeks.map((col, i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {col.map((d) => {
                const key = iso(d)
                const future = d > today
                const done = days[key]
                return (
                  <div key={key} title={key}
                    style={{
                      width: 15, height: 15, borderRadius: 4,
                      background: future ? 'transparent'
                        : done ? c : 'var(--line)',
                      opacity: future ? 0.25 : 1,
                    }} />
                )
              })}
            </div>
          ))}
        </div>
      )}

      {h.note && <p className="form-hint" style={{ marginTop: 14 }}>{h.note}</p>}
      <p className="form-hint" style={{ marginTop: 10, color: 'var(--ink-faint)' }}>
        {h.goal_type === 'weekly'
          ? `Goal: ${h.weekly_target} day${h.weekly_target === 1 ? '' : 's'} a week`
          : h.goal_type === 'weekdays' ? 'Goal: on chosen weekdays'
            : 'Goal: every day'}
        {h.target > 1 && h.unit ? ` · ${h.target} ${h.unit}/day` : ''}
      </p>
    </Sheet>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="card" style={{ flex: 1, padding: 12, textAlign: 'center' }}>
      <div className="mcard-label">{label}</div>
      <div className="tabnum" style={{ fontSize: 19, fontWeight: 800, color }}>{value}</div>
    </div>
  )
}
