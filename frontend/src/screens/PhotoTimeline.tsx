/** The gallery's timeline: date-grouped, justified, virtualised.
 *
 *  WHY JUSTIFIED AND NOT A SQUARE GRID
 *  A square grid centre-crops every tile, so a panorama and a portrait both
 *  become the same square and you cannot tell them apart until you open them.
 *  A justified layout keeps each photo's real shape and varies the tile width
 *  instead, packing each row to exactly the full width. That shape-preserving
 *  row is the thing that makes a photo library read as a photo library, and it
 *  is what every mature photo app uses.
 *
 *  WHY VIRTUALISED
 *  The old grid mounted an <img> per photo. At a few hundred that is merely
 *  wasteful; at the 1,000,000-item target in ARCHITECTURE.md the browser dies.
 *  Only the rows near the viewport are mounted here, so scroll cost is flat in
 *  library size.
 *
 *  WHY IT LISTENS TO SCROLL IN THE CAPTURE PHASE
 *  Scroll does not bubble. This component cannot know whether the page, the
 *  app shell or some inner pane is the thing that actually scrolls, and that
 *  has changed in this app before. A capturing window listener sees the event
 *  whichever element produces it, so the timeline keeps working if the layout
 *  is rearranged around it.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Photo } from '../types'

/** Target row height per density. The layout treats this as "about this tall" —
 *  every row is then scaled so it ends exactly at the container's edge. */
const ROW_H: Record<string, number> = { small: 110, grid: 170, large: 260 }
const GAP = 4
/** Rows rendered beyond the viewport, so a fast flick lands on drawn tiles
 *  rather than blank space that fills in a frame later. */
const OVERSCAN = 6

type Tile = { p: Photo; aspect: number; w: number }
type Row = { kind: 'row'; items: Tile[]; h: number; y: number; key: string }
type Head = { kind: 'head'; label: string; day: string; ids: number[]; y: number; h: number; key: string }
type Block = Row | Head

/** A photo with no stored dimensions still has to occupy a sensible shape.
 *  4:3 is the commonest camera aspect and reads as unremarkable; collapsing
 *  the row (aspect 0) or guessing square would both be visibly wrong. */
function aspectOf(p: Photo): number {
  const w = p.width || 0
  const h = p.height || 0
  if (w > 0 && h > 0) return Math.min(Math.max(w / h, 0.4), 3.2)
  return 4 / 3
}

function dayKey(p: Photo): string {
  return (p.taken_at || '').slice(0, 10) || 'unknown'
}

function dayLabel(key: string): string {
  if (key === 'unknown') return 'No date'
  const d = new Date(key + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return key
  const today = new Date()
  const t0 = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const diff = Math.round((t0.getTime() - d.getTime()) / 86400000)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Yesterday'
  const sameYear = d.getFullYear() === today.getFullYear()
  return d.toLocaleDateString(undefined, {
    weekday: 'short', day: 'numeric', month: 'long',
    ...(sameYear ? {} : { year: 'numeric' }),
  })
}

function durationLabel(ms?: number | null): string | null {
  if (!ms || ms <= 0) return null
  const total = Math.round(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

/** Pack photos into rows that each end exactly at `width`.
 *
 *  A row is closed as soon as the photos in it, scaled to the target height,
 *  would overflow — then the whole row is rescaled down so it fits precisely.
 *  The final row is NOT stretched: one leftover photo blown up to full width
 *  is the classic justified-layout tell, and it looks like a mistake.
 */
function layout(photos: Photo[], width: number, target: number): Block[] {
  const out: Block[] = []
  if (width <= 0) return out

  const groups: { day: string; items: Photo[] }[] = []
  for (const p of photos) {
    const k = dayKey(p)
    const last = groups[groups.length - 1]
    if (last && last.day === k) last.items.push(p)
    else groups.push({ day: k, items: [p] })
  }

  let y = 0
  for (const g of groups) {
    const headH = 44
    out.push({
      kind: 'head', label: dayLabel(g.day), day: g.day,
      ids: g.items.map((p) => p.id), y, h: headH, key: `h:${g.day}`,
    })
    y += headH

    let cur: { p: Photo; aspect: number }[] = []
    let sum = 0
    const flush = (stretch: boolean) => {
      if (!cur.length) return
      const gaps = GAP * (cur.length - 1)
      const h = stretch
        ? (width - gaps) / sum
        : Math.min(target, (width - gaps) / sum)
      const items: Tile[] = cur.map((c) => ({ p: c.p, aspect: c.aspect, w: c.aspect * h }))
      out.push({ kind: 'row', items, h, y, key: `r:${g.day}:${items[0].p.id}` })
      y += h + GAP
      cur = []; sum = 0
    }
    for (const p of g.items) {
      const a = aspectOf(p)
      cur.push({ p, aspect: a }); sum += a
      if (sum * target + GAP * (cur.length - 1) >= width) flush(true)
    }
    flush(false)
    y += 12   // breathing room before the next day
  }
  return out
}

export function PhotoTimeline({ photos, onOpen, selected, onToggle, onSelectDay, density }: {
  photos: Photo[]
  onOpen: (p: Photo) => void
  /** Selected ids. Omit entirely to render a plain, non-selectable timeline. */
  selected?: Set<number>
  onToggle?: (p: Photo) => void
  onSelectDay?: (ids: number[], on: boolean) => void
  density: string
}) {
  const host = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)
  const [range, setRange] = useState<[number, number]>([0, 40])

  // Width drives the whole layout, so it is measured rather than assumed.
  useLayoutEffect(() => {
    const el = host.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const target = ROW_H[density] ?? ROW_H.grid
  const blocks = useMemo(() => layout(photos, width, target), [photos, width, target])
  const total = blocks.length ? blocks[blocks.length - 1].y + blocks[blocks.length - 1].h : 0

  const recompute = useCallback(() => {
    const el = host.current
    if (!el || !blocks.length) return
    const top = -el.getBoundingClientRect().top
    const lo = top - OVERSCAN * target
    const hi = top + window.innerHeight + OVERSCAN * target
    let a = 0
    let b = blocks.length - 1
    // Blocks are already sorted by y, so a scan from both ends is enough and
    // avoids a binary search over a list that changes shape on every resize.
    while (a < blocks.length && blocks[a].y + blocks[a].h < lo) a++
    while (b > a && blocks[b].y > hi) b--
    setRange([Math.max(0, a - 1), Math.min(blocks.length - 1, b + 1)])
  }, [blocks, target])

  useEffect(() => {
    recompute()
    // Capture phase: scroll does not bubble, and the scrolling element here is
    // not this component's to know. See the note at the top of the file.
    window.addEventListener('scroll', recompute, true)
    window.addEventListener('resize', recompute)
    return () => {
      window.removeEventListener('scroll', recompute, true)
      window.removeEventListener('resize', recompute)
    }
  }, [recompute])

  const selecting = !!selected && selected.size > 0

  return (
    // The surface matters. This app draws a decorative landscape behind its
    // screens, and with a square edge-to-edge grid that never showed. A
    // justified layout has gaps between rows and a short final row per day, so
    // the artwork came through between the photos and competed with them. A
    // photo library has to be a neutral ground or every picture is read against
    // whatever happens to be behind it.
    <div className="ptl-surface">
    <div className="ptl" ref={host} style={{ height: total }}>
      {blocks.slice(range[0], range[1] + 1).map((b) => {
        if (b.kind === 'head') {
          const all = !!selected && b.ids.length > 0 && b.ids.every((i) => selected.has(i))
          return (
            <div className="ptl-head" key={b.key} style={{ top: b.y, height: b.h }}>
              {onSelectDay && (
                <button className={`ptl-check head${all ? ' on' : ''}`}
                  onClick={() => onSelectDay(b.ids, !all)}
                  aria-label={all ? `Deselect ${b.label}` : `Select ${b.label}`}>
                  <Tick />
                </button>
              )}
              <span className="ptl-head-label">{b.label}</span>
              <span className="ptl-head-count">{b.ids.length}</span>
              <span className="ptl-head-rule" />
            </div>
          )
        }
        return (
          <div className="ptl-row" key={b.key} style={{ top: b.y, height: b.h }}>
            {b.items.map((t) => {
              const p = t.p
              const isVideo = p.kind === 'video'
              const dur = durationLabel(p.duration_ms)
              const on = !!selected?.has(p.id)
              return (
                <div className={`ptl-tile${on ? ' sel' : ''}`} key={p.id}
                  style={{ width: t.w, height: b.h }}>
                  <button className="ptl-hit"
                    onClick={() => (selecting && onToggle ? onToggle(p) : onOpen(p))}
                    aria-label={p.caption || (isVideo ? 'Video' : 'Photo')}>
                    <img src={p.thumb_url || p.url} loading="lazy" decoding="async" alt="" />
                  </button>
                  {onToggle && (
                    <button className={`ptl-check${on ? ' on' : ''}`}
                      onClick={(e) => { e.stopPropagation(); onToggle(p) }}
                      aria-label={on ? 'Deselect' : 'Select'}>
                      <Tick />
                    </button>
                  )}
                  {!!p.is_favourite && <span className="ptl-fav">★</span>}
                  {isVideo && (
                    <span className="ptl-vid">
                      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor" /></svg>
                      {dur && <em>{dur}</em>}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
    </div>
  )
}

function Tick() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor"
        strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
