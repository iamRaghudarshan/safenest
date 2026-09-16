// Inset grouped list — the settings pattern every phone OS uses.
//
// One rounded card per group with hairline-separated rows, rather than a card per
// item. That is what makes a settings screen read as a settings screen: related
// controls sit together, the eye follows a single left edge, and the page stops
// looking like a pile of loose tiles.
import { useState, type ReactNode } from 'react'

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

/** A row that opens to reveal more, instead of a section that is always open.
 *
 *  Profile had grown to eighteen top-level groups, and the ones people needed
 *  most — their name, their password, the address to type into a phone — were
 *  buried under diagnostics that matter perhaps twice in a copy's life: firewall
 *  state, previous computers, per-module byte counts. Deleting those would be
 *  worse, because each exists for a failure that really happened and fails
 *  invisibly when it does. So they collapse.
 *
 *  `attention` is the reason this is not merely cosmetic: a collapsed section
 *  that hides a *problem* is a regression, so a caller that knows something is
 *  wrong passes it and the row carries a badge whether or not anyone opens it.
 *
 *  It badges rather than auto-expanding, which was the first attempt. On a
 *  machine reached only through its public address the firewall block is real,
 *  permanent and irrelevant — auto-expanding meant a wall of Windows instructions
 *  greeting every visit to Profile, which is the thing this was meant to fix. A
 *  dot says the same thing in eight pixels and costs one tap to read.
 */
export function SettingsDisclosure({
  icon, tint, label, sub, value, attention, children,
}: {
  icon: ReactNode
  tint?: string
  label: string
  sub?: ReactNode
  value?: ReactNode
  /** Something in here needs looking at: badge the row. */
  attention?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="set-row" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}>
        <span className="set-ic" style={tint ? { background: tint } : undefined}>{icon}</span>
        <span className="set-text">
          <span className="set-label">{label}</span>
          {sub && <span className="set-sub">{sub}</span>}
        </span>
        {value != null && <span className="set-value">{value}</span>}
        {attention && !open && <span className="set-alert" aria-label="Needs attention" />}
        <span className={`set-chev set-disc${open ? ' open' : ''}`} aria-hidden="true">›</span>
      </button>
      {/* Children are rendered raw, not wrapped in a block, because a disclosure
          holding plain rows (a storage breakdown) and one holding custom content
          (firewall buttons) need different padding. Callers wrap what needs it. */}
      {open && children}
    </>
  )
}
