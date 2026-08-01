// Pinch / double-tap / wheel zoom with panning, for full-screen image viewers.
// Kept dependency-free and touch-first: phones are the primary way this app is used.
import { useCallback, useEffect, useRef, useState } from 'react'

interface T { s: number; x: number; y: number }
const IDENT: T = { s: 1, x: 0, y: 0 }
const MAX_SCALE = 6
const DOUBLE_TAP_SCALE = 2.5

export function Zoomable({ src, alt, onZoomChange, onSingleTap, fill }: {
  src: string
  alt?: string
  /** Fires when zoom enters/leaves 1x — lets the parent disable swipe-to-dismiss. */
  onZoomChange?: (zoomed: boolean) => void
  /** A confirmed single tap (double-taps are swallowed by the zoom toggle). */
  onSingleTap?: () => void
  /** Stretch to fill the parent instead of sitting in a capped box. */
  fill?: boolean
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const img = useRef<HTMLImageElement>(null)
  const [t, setT] = useState<T>(IDENT)
  const tRef = useRef(t)
  tRef.current = t

  const g = useRef({
    mode: '' as '' | 'pan' | 'pinch',
    px: 0, py: 0, x0: 0, y0: 0,   // pointer start + transform at gesture start
    dist0: 0, s0: 1, fx: 0, fy: 0, // pinch baseline + focal point
    lastTap: 0, moved: false,
  })

  // Reset when the picture changes.
  useEffect(() => { setT(IDENT) }, [src])
  useEffect(() => { onZoomChange?.(t.s > 1.01) }, [t.s, onZoomChange])

  /** Keep scale in range and stop the image being dragged off-screen. */
  const clamp = useCallback((n: T): T => {
    const s = Math.min(MAX_SCALE, Math.max(1, n.s))
    const box = wrap.current?.getBoundingClientRect()
    const el = img.current
    if (!box || !el || s <= 1) return { s, x: 0, y: 0 }
    // Pan range is however much the scaled image overflows its container.
    const ox = Math.max(0, (el.offsetWidth * s - box.width) / 2)
    const oy = Math.max(0, (el.offsetHeight * s - box.height) / 2)
    return { s, x: Math.max(-ox, Math.min(ox, n.x)), y: Math.max(-oy, Math.min(oy, n.y)) }
  }, [])

  /** Scale about a focal point so the pixel under the fingers stays put. */
  const zoomAt = useCallback((nextScale: number, fx: number, fy: number, base: T) => {
    const box = wrap.current?.getBoundingClientRect()
    if (!box) return
    const cx = fx - box.left - box.width / 2
    const cy = fy - box.top - box.height / 2
    const k = nextScale / base.s
    setT(clamp({ s: nextScale, x: cx - (cx - base.x) * k, y: cy - (cy - base.y) * k }))
  }, [clamp])

  const toggleZoom = useCallback((fx: number, fy: number) => {
    const cur = tRef.current
    if (cur.s > 1.01) setT(IDENT)
    else zoomAt(DOUBLE_TAP_SCALE, fx, fy, cur)
  }, [zoomAt])

  type Pt = { clientX: number; clientY: number }
  const dist = (a: Pt, b: Pt) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)

  function onTouchStart(e: React.TouchEvent) {
    const st = g.current
    if (e.touches.length === 2) {
      const [a, b] = [e.touches[0], e.touches[1]]
      st.mode = 'pinch'
      st.dist0 = dist(a, b)
      st.s0 = tRef.current.s
      st.fx = (a.clientX + b.clientX) / 2
      st.fy = (a.clientY + b.clientY) / 2
      st.moved = true
    } else if (e.touches.length === 1) {
      const p = e.touches[0]
      st.mode = tRef.current.s > 1.01 ? 'pan' : ''
      st.px = p.clientX; st.py = p.clientY
      st.x0 = tRef.current.x; st.y0 = tRef.current.y
      st.moved = false
    }
  }

  function onTouchMove(e: React.TouchEvent) {
    const st = g.current
    if (st.mode === 'pinch' && e.touches.length === 2) {
      e.preventDefault()
      const d = dist(e.touches[0], e.touches[1])
      if (st.dist0 > 0) zoomAt(st.s0 * (d / st.dist0), st.fx, st.fy, { ...tRef.current, s: st.s0, x: st.x0, y: st.y0 })
    } else if (st.mode === 'pan' && e.touches.length === 1) {
      e.preventDefault()
      const p = e.touches[0]
      const dx = p.clientX - st.px, dy = p.clientY - st.py
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) st.moved = true
      setT(clamp({ s: tRef.current.s, x: st.x0 + dx, y: st.y0 + dy }))
    }
  }

  // A single tap must wait to see whether a second one follows, otherwise every
  // double-tap-to-zoom would also fire the single-tap action.
  const tapTimer = useRef<number | null>(null)
  useEffect(() => () => { if (tapTimer.current) clearTimeout(tapTimer.current) }, [])

  function registerTap(x: number, y: number) {
    const st = g.current
    const now = Date.now()
    if (now - st.lastTap < 300) {
      if (tapTimer.current) { clearTimeout(tapTimer.current); tapTimer.current = null }
      st.lastTap = 0
      toggleZoom(x, y)
      return
    }
    st.lastTap = now
    if (onSingleTap) {
      tapTimer.current = window.setTimeout(() => { tapTimer.current = null; onSingleTap() }, 300)
    }
  }

  function onTouchEnd(e: React.TouchEvent) {
    const st = g.current
    // Only a tap that didn't travel counts — anything else was a pan.
    if (!st.moved && e.changedTouches.length === 1 && st.mode !== 'pinch') {
      const p = e.changedTouches[0]
      registerTap(p.clientX, p.clientY)
    }
    if (e.touches.length === 0) st.mode = ''
  }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault()
    const cur = tRef.current
    zoomAt(cur.s * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY, cur)
  }

  // Desktop drag-to-pan.
  function onMouseDown(e: React.MouseEvent) {
    if (tRef.current.s <= 1.01) return
    e.preventDefault()
    const sx = e.clientX, sy = e.clientY
    const { x: bx, y: by, s } = tRef.current
    const move = (m: MouseEvent) => setT(clamp({ s, x: bx + (m.clientX - sx), y: by + (m.clientY - sy) }))
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const zoomed = t.s > 1.01
  return (
    <div
      ref={wrap}
      className={`zoomwrap${fill ? ' fill' : ''}`}
      style={{ touchAction: zoomed ? 'none' : 'pan-y', cursor: zoomed ? 'grab' : 'zoom-in' }}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onWheel={onWheel}
      onMouseDown={onMouseDown}
      onClick={(e) => { if (e.detail === 1) registerTap(e.clientX, e.clientY) }}
      onDoubleClick={(e) => toggleZoom(e.clientX, e.clientY)}
    >
      <img
        ref={img}
        src={src}
        alt={alt || ''}
        draggable={false}
        style={{
          transform: `translate3d(${t.x}px, ${t.y}px, 0) scale(${t.s})`,
          transition: g.current.mode ? 'none' : 'transform .22s cubic-bezier(.2,.8,.3,1)',
        }}
      />
      {zoomed && (
        <button className="zoom-badge" onClick={(e) => { e.stopPropagation(); setT(IDENT) }}>
          {t.s.toFixed(1)}× · Reset
        </button>
      )}
    </div>
  )
}
