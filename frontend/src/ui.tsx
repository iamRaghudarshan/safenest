// Reusable UI primitives: TopBar, Sheet (bottom modal), form fields, toast, confirm.
import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useOverlayBack } from './nav'

export function TopBar({ title, sub, onBack, right }: {
  title: string; sub?: string; onBack?: () => void; right?: ReactNode
}) {
  return (
    <div className="topbar">
      {onBack && <button className="back" onClick={onBack} aria-label="Back">‹</button>}
      {/* min-width:0 lives on this wrapper: without it a long title refuses to
          shrink and shoves the trailing controls out of the bar. */}
      <div className="topbar-text">
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {right && <div className="topbar-actions">{right}</div>}
    </div>
  )
}

/**
 * Keep an element matched to the VISUAL viewport.
 *
 * When the on-screen keyboard opens, the layout viewport is unchanged but the
 * visual viewport shrinks to the space above the keyboard. A bottom-anchored
 * sheet therefore stays pinned to a bottom edge that is now behind the keyboard,
 * hiding its inputs and Save button. Sizing to visualViewport (and shifting by
 * its offsetTop, which iOS uses when it scrolls the page under the keyboard)
 * keeps the whole sheet in the area the user can actually see.
 */
function useVisualViewport(ref: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const vv = window.visualViewport
    const el = ref.current
    if (!vv || !el) return
    const apply = () => {
      el.style.height = `${vv.height}px`
      el.style.transform = `translateY(${vv.offsetTop}px)`
    }
    apply()
    vv.addEventListener('resize', apply)
    vv.addEventListener('scroll', apply)
    return () => {
      vv.removeEventListener('resize', apply)
      vv.removeEventListener('scroll', apply)
    }
  }, [ref])
}

export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useOverlayBack(onClose) // back gesture / button dismisses the sheet
  const scrim = useRef<HTMLDivElement>(null)
  useVisualViewport(scrim)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = '' }
  }, [onClose])

  // Portal to <body> so no ancestor transform (e.g. the pull-to-refresh wrapper)
  // or overflow can clip/mis-position the fixed sheet or break its scroll.
  return createPortal(
    <div className="scrim" ref={scrim} onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="grab" />
        {/* An explicit close button: the drag handle and tap-outside are not
            discoverable, and on a phone the sheet often covers the whole screen
            so there is no visible "outside" left to tap. */}
        <div className="sheet-head">
          <h2>{title}</h2>
          <button className="sheet-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="field"><label>{label}</label>{children}</div>
}

export function Money({ value, onChange, placeholder }: {
  value: string | number; onChange: (v: string) => void; placeholder?: string
}) {
  return (
    <div className="money">
      <input className="input" inputMode="decimal" placeholder={placeholder || '0'}
        value={value} onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ''))} />
    </div>
  )
}

export function Segment<T extends string>({ value, options, onChange }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void
}) {
  return (
    <div className="segment">
      {options.map((o) => (
        <button key={o.value} type="button" className={value === o.value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** An empty state, optionally with the one thing to do about it.
 *
 *  The action matters where the screen is empty precisely because nothing has been
 *  added yet: describing the button someone should find ("Tap Upload") asks them to
 *  go looking, when the button could simply be here.
 */
export function Empty({ icon, title, hint, action }: {
  icon: string; title: string; hint?: string
  action?: { label: string; onClick: () => void }
}) {
  return (
    <div className="empty">
      <div className="big">{icon}</div>
      <div style={{ fontWeight: 700, color: 'var(--ink-soft)' }}>{title}</div>
      {hint && <div style={{ marginTop: 4, fontSize: 13 }}>{hint}</div>}
      {action && (
        <button className="btn" style={{ marginTop: 14 }} onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  )
}

export function Spinner() { return <div className="spinner" /> }
