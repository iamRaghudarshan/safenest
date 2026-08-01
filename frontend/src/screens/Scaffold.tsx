// Shared list-screen scaffold: topbar with back, loading/empty states, FAB,
// and the edit/delete row menu used across the finance modules.
import { useState, type ReactNode } from 'react'
import { useNav } from '../nav'
import { useAuth } from '../auth'
import { MODULES } from '../modules'
import { TopBar, Empty } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { IcEdit, IcTrash } from '../icons'
import type { ModuleKey } from '../types'

/** Placeholder cards while the list loads. Showing the shape of what is coming
 *  reads as "loading" far more clearly than a lone spinner on a blank screen. */
function Skeleton() {
  return (
    <div className="skel-list" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className="skel-card">
          <div className="skel-row">
            <span className="skel-ic" />
            <div style={{ flex: 1 }}>
              <span className="skel-line" style={{ width: '52%' }} />
              <span className="skel-line sm" style={{ width: '34%' }} />
            </div>
          </div>
          <span className="skel-line lg" style={{ width: '68%' }} />
        </div>
      ))}
    </div>
  )
}

/** Empty state carrying the module's own icon, colour, purpose and primary action. */
function ModuleEmpty({ mod, onAdd, canCreate }: {
  mod: ModuleKey; onAdd?: () => void; canCreate: boolean
}) {
  const m = MODULES[mod]
  const Icon = m.Icon
  return (
    <div className="mod-empty">
      <div className="mod-empty-ic" style={{ background: m.color }}><Icon /></div>
      <h2 className="mod-empty-t">No {m.label.toLowerCase()} yet</h2>
      <p className="mod-empty-s">{m.blurb}</p>
      {canCreate && onAdd && (
        <button className="btn mod-empty-btn" onClick={onAdd}>＋ {m.addLabel}</button>
      )}
    </div>
  )
}

/** Shown when the list could not be loaded. Critically this is NOT the empty
 *  state: telling someone "no loans yet" because the server is unreachable reads
 *  as "your data is gone", which is alarming and untrue. */
function LoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const offline = /offline|reach|connection/i.test(message)
  return (
    <div className="mod-empty">
      <div className="mod-empty-ic err">{offline ? '📡' : '⚠️'}</div>
      <h2 className="mod-empty-t">{offline ? 'Can’t load right now' : 'Something went wrong'}</h2>
      <p className="mod-empty-s">{message}</p>
      <p className="mod-empty-note">Your data is safe — it just can’t be fetched at the moment.</p>
      {onRetry && <button className="btn mod-empty-btn" onClick={onRetry}>Try again</button>}
    </div>
  )
}

export function ModuleScreen({ mod, sub, loading, empty, error, onRetry, children, onAdd, onRefresh, headerRight }: {
  mod: ModuleKey; sub?: string; loading?: boolean; empty?: boolean
  error?: string; onRetry?: () => void
  children: ReactNode; onAdd?: () => void; onRefresh?: () => Promise<unknown> | void; headerRight?: ReactNode
}) {
  const { back, canBack } = useNav()
  const { can } = useAuth()
  const m = MODULES[mod]
  const canCreate = can(mod)

  // `children` must render even when the list is empty: every screen passes its
  // add/edit sheet as a child, so dropping them here made the + button do nothing
  // for anyone who had no records yet — the exact people the empty state is
  // telling to add their first one.
  const body = loading
    ? <Skeleton />
    : (
      <>
        <div className="list">{children}</div>
        {/* After the list, so a screen's own header content (e.g. the Expenses
            month summary) still sits above the "nothing here yet" message.
            A load failure takes priority over "empty" — see LoadError. */}
        {error && empty
          ? <LoadError message={error} onRetry={onRetry} />
          : empty && <ModuleEmpty mod={mod} onAdd={onAdd} canCreate={canCreate} />}
      </>
    )

  return (
    <div className="screen">
      <TopBar title={m.label} sub={sub} onBack={canBack ? back : undefined} right={headerRight} />
      {/* FAB stays outside PullToRefresh so its transform doesn't move the fixed button */}
      {onRefresh ? <PullToRefresh onRefresh={onRefresh}>{body}</PullToRefresh> : body}
      {onAdd && canCreate && <button className="fab" onClick={onAdd} aria-label={m.addLabel}>+</button>}
    </div>
  )
}

// A list row with an accent icon tile, title/subtitle, right-aligned value, and a tap menu for edit/delete.
export function Row({ color, icon, title, subtitle, right, onEdit, onDelete }: {
  color: string; icon: ReactNode; title: string; subtitle?: ReactNode; right?: ReactNode
  onEdit?: () => void; onDelete?: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="rowitem" onClick={() => (onEdit || onDelete) && setOpen((o) => !o)}>
        <div className="grip" style={{ background: color }}>{icon}</div>
        <div className="main">
          <div className="t">{title}</div>
          {subtitle && <div className="s">{subtitle}</div>}
        </div>
        {right && <div className="amt">{right}</div>}
      </div>
      {open && (onEdit || onDelete) && (
        <div className="swipe-actions">
          {onEdit && <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => { setOpen(false); onEdit() }}><IcEdit className="ic" /> Edit</button>}
          {onDelete && <button className="btn danger sm" style={{ flex: 1 }} onClick={() => { setOpen(false); onDelete() }}><IcTrash className="ic" /> Delete</button>}
        </div>
      )}
    </div>
  )
}

/**
 * "History" link for one record — opens the Activity log filtered to just this item.
 *
 * The nav layer carries a single one-shot string between screens, so the filter
 * travels as JSON inside it rather than needing a wider nav API.
 */
export function HistoryLink({ entity, id, label, block }: {
  entity: string; id: number; label?: string | null; block?: boolean
}) {
  const { go } = useNav()
  return (
    <button
      className={`btn ghost sm${block ? ' block' : ''}`}
      style={block ? { marginTop: 10 } : { flex: 1 }}
      onClick={(e) => {
        e.stopPropagation()
        go('activity', JSON.stringify({ entity, id, label: label || null }))
      }}>
      ⟲ History
    </button>
  )
}

// Re-exported so screens that build their own layout keep using the same primitive.
export { Empty }
