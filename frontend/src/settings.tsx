// Inset grouped list — the settings pattern every phone OS uses.
//
// One rounded card per group with hairline-separated rows, rather than a card per
// item. That is what makes a settings screen read as a settings screen: related
// controls sit together, the eye follows a single left edge, and the page stops
// looking like a pile of loose tiles.
import type { ReactNode } from 'react'

export function SettingsGroup({ title, footer, children }: {
  title?: string
  footer?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="set-group">
      {title && <h2 className="set-head">{title}</h2>}
      <div className="set-card">{children}</div>
      {footer && <p className="set-foot">{footer}</p>}
    </section>
  )
}

export function SettingsRow({ icon, tint, label, sub, value, onClick, right, danger }: {
  icon: ReactNode
  /** Any CSS colour; tints the icon tile so rows are scannable by colour. */
  tint?: string
  label: string
  sub?: ReactNode
  /** Right-aligned read-only value, as in "Cached  12.4 MB". */
  value?: ReactNode
  onClick?: () => void
  /** Replaces the chevron — a switch, a spinner, whatever the row needs. */
  right?: ReactNode
  danger?: boolean
}) {
  const body = (
    <>
      <span className="set-ic" style={tint ? { background: tint } : undefined}>{icon}</span>
      <span className="set-text">
        <span className={`set-label${danger ? ' danger' : ''}`}>{label}</span>
        {sub && <span className="set-sub">{sub}</span>}
      </span>
      {value != null && <span className="set-value">{value}</span>}
      {right ?? (onClick && <span className="set-chev" aria-hidden="true">›</span>)}
    </>
  )
  return onClick
    ? <button type="button" className="set-row" onClick={onClick}>{body}</button>
    : <div className="set-row">{body}</div>
}

/** A full-width block inside a group — for a segment, a note, or custom content. */
export function SettingsBlock({ children }: { children: ReactNode }) {
  return <div className="set-block">{children}</div>
}
