// Swipeable list row: drag right to complete (toggle), left to delete. The action
// is revealed behind the card. Native touch listeners are scoped to this row and
// only stop propagation once a swipe is clearly HORIZONTAL, so vertical scroll,
// pull-to-refresh, and the left-edge back gesture keep working.
import { useEffect, useRef, useState, type ReactNode } from 'react'

const THRESH = 80 // px to trigger an action
const CAP = 140   // rubber-band cap

export function SwipeRow({ onSwipeRight, onSwipeLeft, rightLabel = 'Done', leftLabel = 'Delete', children }: {
  onSwipeRight?: () => void
  onSwipeLeft?: () => void
  rightLabel?: string
  leftLabel?: string
  children: ReactNode
}) {
  const [dx, setDx] = useState(0)
  const [dragging, setDragging] = useState(false)
  const fg = useRef<HTMLDivElement>(null)
  const st = useRef({ x: 0, y: 0, horiz: false, active: false, dx: 0 })

  useEffect(() => {
    const el = fg.current
    if (!el) return
    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const t = e.touches[0]
      st.current = { x: t.clientX, y: t.clientY, horiz: false, active: true, dx: 0 }
    }
    const onMove = (e: TouchEvent) => {
      const s = st.current
      if (!s.active) return
      const t = e.touches[0]
      const dX = t.clientX - s.x, dY = t.clientY - s.y
      if (!s.horiz) {
        if (Math.abs(dX) > 10 && Math.abs(dX) > Math.abs(dY) * 1.3) { s.horiz = true; setDragging(true) }
        else if (Math.abs(dY) > 10) { s.active = false; return } // vertical → let it scroll / pull
        else return
      }
      e.stopPropagation()
      if (e.cancelable) e.preventDefault()
      let d = dX
      // only allow directions that have a handler
      if ((d > 0 && !onSwipeRight) || (d < 0 && !onSwipeLeft)) d = 0
      if (Math.abs(d) > CAP) d = Math.sign(d) * (CAP + (Math.abs(d) - CAP) * 0.2)
      s.dx = d
      setDx(d)
    }
    const onEnd = (e: TouchEvent) => {
      const s = st.current
      if (s.horiz) e.stopPropagation()
      const d = s.dx
      setDragging(false)
      setDx(0)
      if (d > THRESH && onSwipeRight) onSwipeRight()
      else if (d < -THRESH && onSwipeLeft) onSwipeLeft()
      s.active = false; s.horiz = false; s.dx = 0
    }
    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd, { passive: true })
    el.addEventListener('touchcancel', onEnd, { passive: true })
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
    }
  }, [onSwipeRight, onSwipeLeft])

  const right = dx >= 0
  const prog = Math.min(Math.abs(dx) / THRESH, 1)
  return (
    <div className="swrow">
      <div className="swrow-bg" style={{
        background: right ? 'var(--ok)' : 'var(--danger)',
        justifyContent: right ? 'flex-start' : 'flex-end',
        opacity: prog,
      }}>
        <span>{right ? `✓ ${rightLabel}` : `${leftLabel}`}</span>
      </div>
      <div ref={fg} className="swrow-fg" style={{
        transform: `translateX(${dx}px)`,
        transition: dragging ? 'none' : 'transform 0.25s cubic-bezier(0.2,0.9,0.3,1)',
      }}>
        {children}
      </div>
    </div>
  )
}
