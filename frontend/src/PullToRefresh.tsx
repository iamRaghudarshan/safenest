// Native-style pull-to-refresh. Wrap a screen's scrollable content in it; when the
// page is scrolled to the top and the user drags down past the threshold, onRefresh
// runs. Content follows the finger with resistance; a spinner shows while refreshing.
import { useEffect, useRef, useState, type ReactNode } from 'react'

const THRESHOLD = 70 // px pulled (after damping) to trigger a refresh
const MAX = 100 // max content shift
const DAMP = 0.5 // finger-to-content resistance

export function PullToRefresh({ onRefresh, children }: {
  onRefresh: () => Promise<unknown> | void
  children: ReactNode
}) {
  const [dist, setDist] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const startY = useRef<number | null>(null)
  const pulling = useRef(false)
  const distRef = useRef(0)
  const refreshingRef = useRef(false)
  refreshingRef.current = refreshing

  useEffect(() => {
    // Which element scrolls depends on the mode: the installed app locks the page
    // and scrolls .screen (so the tab bar can't drift), a browser tab scrolls the
    // page itself (so the toolbar can collapse). Detect it rather than assume.
    const scroller = (): { scrollTop: number } => {
      const el = wrap.current?.closest('.screen') as HTMLElement | null
      if (el && getComputedStyle(el).overflowY === 'auto') return el
      return document.scrollingElement || document.documentElement
    }
    const onStart = (e: TouchEvent) => {
      if (refreshingRef.current || e.touches.length !== 1) return
      if (scroller().scrollTop <= 0) {
        startY.current = e.touches[0].clientY
        pulling.current = true
      }
    }
    const onMove = (e: TouchEvent) => {
      if (!pulling.current || startY.current == null) return
      const dy = e.touches[0].clientY - startY.current
      if (dy > 0 && scroller().scrollTop <= 0) {
        const d = Math.min(dy * DAMP, MAX)
        distRef.current = d
        setDist(d)
        if (e.cancelable) e.preventDefault() // take over from native overscroll
      } else if (dy <= 0) {
        pulling.current = false
        distRef.current = 0
        setDist(0)
      }
    }
    const onEnd = () => {
      if (!pulling.current) return
      pulling.current = false
      startY.current = null
      if (distRef.current >= THRESHOLD) {
        setRefreshing(true)
        setDist(THRESHOLD)
        Promise.resolve(onRefresh()).finally(() => {
          setRefreshing(false)
          distRef.current = 0
          setDist(0)
        })
      } else {
        distRef.current = 0
        setDist(0)
      }
    }
    document.addEventListener('touchstart', onStart, { passive: true })
    document.addEventListener('touchmove', onMove, { passive: false })
    document.addEventListener('touchend', onEnd, { passive: true })
    document.addEventListener('touchcancel', onEnd, { passive: true })
    return () => {
      document.removeEventListener('touchstart', onStart)
      document.removeEventListener('touchmove', onMove)
      document.removeEventListener('touchend', onEnd)
      document.removeEventListener('touchcancel', onEnd)
    }
  }, [onRefresh])

  const shift = refreshing ? 46 : dist
  const ready = dist >= THRESHOLD
  return (
    <div className="ptr" ref={wrap} style={{
      transform: `translateY(${shift}px)`,
      transition: pulling.current ? 'none' : 'transform 0.28s cubic-bezier(0.2,0.9,0.3,1)',
    }}>
      <div className="ptr-badge" style={{ opacity: shift > 6 ? 1 : 0 }}>
        {refreshing
          ? <span className="ptr-spin" />
          : <span className="ptr-arrow" style={{ transform: `rotate(${ready ? 180 : 0}deg)` }}>↓</span>}
      </div>
      {children}
    </div>
  )
}
