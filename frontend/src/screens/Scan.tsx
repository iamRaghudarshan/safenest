// Document scanner — live camera, real-time page detection, auto-capture,
// perspective correction, and a proper page manager with full-screen preview.
//
// Flow:  camera -> crop (adjust corners) -> pages (manage) -> preview (per page)
//        -> details -> saved as one multi-page PDF.
//
// Every page keeps its ORIGINAL frame plus the corners/rotation/filter chosen for
// it, so any page can be re-cropped, rotated or re-filtered later without quality
// loss — see scanner/process.ts.
import { useCallback, useEffect, useRef, useState } from 'react'
import { tokenStore } from '../api'
import { useToast } from '../toast'
import { TopBar, Field, Spinner } from '../ui'
import { useOverlayBack } from '../nav'
import { Zoomable } from '../Zoomable'
import { DETECT_W, detect, quadDrift, sample, scaleQuad, type Detection } from '../scanner/detect'
import { type Pt, type Quad } from '../scanner/geometry'
import {
  FILTERS, canvasToBlob, fullQuad, renderPage, type Filter, type Rotation,
} from '../scanner/process'
import type { DocumentItem } from '../types'

const STABLE_MS = 900   // framing must hold this long before auto-capture
const STABLE_DRIFT = 6  // px of corner movement (detector space) still counted as "still"

interface Page {
  id: number
  raw: Blob          // the original captured frame
  rawUrl: string
  rawW: number
  rawH: number
  quad: Quad         // corners in raw-frame coordinates
  rotation: Rotation
  filter: Filter
  url: string        // rendered result
  blob: Blob
}

type Step = 'camera' | 'crop' | 'pages' | 'preview' | 'details'

/** A capture waiting to be cropped — either brand new, or a page being re-cropped. */
interface Draft {
  raw: Blob; rawUrl: string; w: number; h: number
  quad: Quad; filter: Filter; rotation: Rotation
  editingId?: number
}

export function ScanFlow({ cats, onClose, onSaved }: {
  cats: { key: string; label: string; emoji: string }[]
  onClose: () => void
  onSaved: (d: DocumentItem) => void
}) {
  const toast = useToast()
  useOverlayBack(onClose)

  const video = useRef<HTMLVideoElement>(null)
  const overlay = useRef<HTMLCanvasElement>(null)
  const workCtx = useRef<CanvasRenderingContext2D | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const raf = useRef(0)
  const seq = useRef(0)
  const filePick = useRef<HTMLInputElement>(null)

  const live = useRef<Detection | null>(null)
  const steadySince = useRef(0)
  const lastQuad = useRef<Quad | null>(null)
  const capturing = useRef(false)

  const [step, setStep] = useState<Step>('camera')
  const [camError, setCamError] = useState('')
  const [auto, setAuto] = useState(true)
  const [torch, setTorch] = useState(false)
  const [hasTorch, setHasTorch] = useState(false)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)

  const [pages, setPages] = useState<Page[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [viewIdx, setViewIdx] = useState(0)

  const [title, setTitle] = useState('')
  const [cat, setCat] = useState(cats[0]?.key || 'other')
  const [expiry, setExpiry] = useState('')
  const [notes, setNotes] = useState('')

  /* ------------------------------------------------------------- camera */
  useEffect(() => {
    if (step !== 'camera') return
    let dead = false
    ;(async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        })
        if (dead) { s.getTracks().forEach((t) => t.stop()); return }
        stream.current = s
        const track = s.getVideoTracks()[0]
        setHasTorch(!!(track?.getCapabilities?.() as { torch?: boolean } | undefined)?.torch)
        if (video.current) { video.current.srcObject = s; await video.current.play().catch(() => {}) }
      } catch {
        setCamError('Camera unavailable — check permissions, or import a photo instead.')
      }
    })()
    return () => {
      dead = true
      cancelAnimationFrame(raf.current)
      stream.current?.getTracks().forEach((t) => t.stop())
      stream.current = null
    }
  }, [step])

  async function toggleTorch() {
    const track = stream.current?.getVideoTracks()[0]
    if (!track) return
    const next = !torch
    try {
      // `torch` is a real constraint on mobile but isn't in the DOM typings yet.
      await track.applyConstraints({ advanced: [{ torch: next }] } as unknown as MediaTrackConstraints)
      setTorch(next)
    } catch { toast('Torch not supported on this camera') }
  }

  const beginCrop = useCallback(async (canvas: HTMLCanvasElement, det: Detection | null) => {
    const raw = await canvasToBlob(canvas, 0.92)
    const quad = det?.confident
      ? scaleQuad(det.quad, DETECT_W, canvas.width)
      : fullQuad(canvas.width, canvas.height)
    setDraft({
      raw, rawUrl: URL.createObjectURL(raw), w: canvas.width, h: canvas.height,
      quad, filter: 'grey', rotation: 0,
    })
    setStep('crop')
  }, [])

  const capture = useCallback(async () => {
    const v = video.current
    if (!v || capturing.current || !v.videoWidth) return
    capturing.current = true
    cancelAnimationFrame(raf.current)
    try {
      const c = document.createElement('canvas')
      c.width = v.videoWidth; c.height = v.videoHeight
      c.getContext('2d')!.drawImage(v, 0, 0)
      await beginCrop(c, live.current)
    } finally { capturing.current = false }
  }, [beginCrop])

  /* ---- per-frame detection ---- */
  useEffect(() => {
    if (step !== 'camera') return
    if (!workCtx.current) {
      workCtx.current = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
    }
    let last = 0
    const tick = (ts: number) => {
      raf.current = requestAnimationFrame(tick)
      const v = video.current, ov = overlay.current
      if (!v || !ov || !v.videoWidth || !workCtx.current) return
      if (ts - last < 90) return // ~11fps: smooth, and leaves the main thread free
      last = ts

      const small = sample(v, v.videoWidth, v.videoHeight, workCtx.current)
      const det = detect(small)
      live.current = det

      if (det?.confident) {
        const drift = lastQuad.current ? quadDrift(det.quad, lastQuad.current) : 999
        lastQuad.current = det.quad
        if (drift <= STABLE_DRIFT) { if (!steadySince.current) steadySince.current = ts }
        else steadySince.current = 0
      } else { steadySince.current = 0; lastQuad.current = null }

      const held = steadySince.current ? ts - steadySince.current : 0
      setReady(!!det?.confident && held > STABLE_MS * 0.35)

      const rect = v.getBoundingClientRect()
      if (ov.width !== rect.width || ov.height !== rect.height) {
        ov.width = rect.width; ov.height = rect.height
      }
      const g = ov.getContext('2d')!
      g.clearRect(0, 0, ov.width, ov.height)
      if (det) {
        // The video is object-fit:cover, so map detector space through the same crop.
        const scale = Math.max(ov.width / small.width, ov.height / small.height)
        const ox = (ov.width - small.width * scale) / 2
        const oy = (ov.height - small.height * scale) / 2
        const pts = det.quad.map((p) => ({ x: p.x * scale + ox, y: p.y * scale + oy }))
        const good = det.confident
        g.beginPath()
        g.moveTo(pts[0].x, pts[0].y)
        for (let i = 1; i < 4; i++) g.lineTo(pts[i].x, pts[i].y)
        g.closePath()
        g.fillStyle = good ? 'rgba(1,118,211,.20)' : 'rgba(255,255,255,.08)'
        g.fill()
        g.lineWidth = 3
        g.strokeStyle = good ? '#1B96FF' : 'rgba(255,255,255,.5)'
        g.stroke()
        if (good) {
          g.fillStyle = '#fff'
          for (const p of pts) { g.beginPath(); g.arc(p.x, p.y, 5, 0, Math.PI * 2); g.fill() }
        }
      }
      if (auto && det?.confident && held > STABLE_MS && !capturing.current) capture()
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [step, auto, capture])

  /* ---- import an existing photo (same crop + dewarp path) ---- */
  async function importFile(files: FileList | null) {
    if (!files?.length) return
    setBusy(true)
    try {
      const bmp = await createImageBitmap(files[0])
      const c = document.createElement('canvas')
      c.width = bmp.width; c.height = bmp.height
      c.getContext('2d')!.drawImage(bmp, 0, 0)
      bmp.close?.()
      const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true })!
      await beginCrop(c, detect(sample(c, c.width, c.height, ctx)))
    } catch { toast('Could not read that image') }
    finally { setBusy(false) }
  }

  /* ------------------------------------------------------- page editing */
  async function commitDraft(d: Draft) {
    setBusy(true)
    try {
      const out = await renderPage(d.raw, d.quad, d.rotation, d.filter)
      setPages((prev) => {
        if (d.editingId != null) {
          return prev.map((p) => {
            if (p.id !== d.editingId) return p
            URL.revokeObjectURL(p.url)
            return { ...p, quad: d.quad, rotation: d.rotation, filter: d.filter, url: out.url, blob: out.blob }
          })
        }
        return [...prev, {
          id: ++seq.current, raw: d.raw, rawUrl: d.rawUrl, rawW: d.w, rawH: d.h,
          quad: d.quad, rotation: d.rotation, filter: d.filter, url: out.url, blob: out.blob,
        }]
      })
      const editing = d.editingId != null
      if (editing) URL.revokeObjectURL(d.rawUrl)
      setDraft(null)
      setStep(editing ? 'preview' : 'camera')
    } catch { toast('Could not process that page') }
    finally { setBusy(false) }
  }

  /** Re-render one existing page after a rotate / filter change. */
  async function updatePage(id: number, patch: Partial<Pick<Page, 'rotation' | 'filter'>>) {
    const page = pages.find((p) => p.id === id)
    if (!page) return
    setBusy(true)
    try {
      const rotation = patch.rotation ?? page.rotation
      const filter = patch.filter ?? page.filter
      const out = await renderPage(page.raw, page.quad, rotation, filter)
      setPages((prev) => prev.map((p) => {
        if (p.id !== id) return p
        URL.revokeObjectURL(p.url)
        return { ...p, rotation, filter, url: out.url, blob: out.blob }
      }))
    } catch { toast('Could not update that page') }
    finally { setBusy(false) }
  }

  function recrop(page: Page) {
    setDraft({
      raw: page.raw, rawUrl: page.rawUrl, w: page.rawW, h: page.rawH,
      quad: page.quad, filter: page.filter, rotation: page.rotation, editingId: page.id,
    })
    setStep('crop')
  }

  function removePage(id: number) {
    setPages((prev) => {
      const p = prev.find((x) => x.id === id)
      if (p) { URL.revokeObjectURL(p.url); URL.revokeObjectURL(p.rawUrl) }
      const next = prev.filter((x) => x.id !== id)
      setViewIdx((i) => Math.max(0, Math.min(i, next.length - 1)))
      if (!next.length) setStep('camera')
      return next
    })
  }

  function move(i: number, dir: -1 | 1) {
    setPages((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const n = [...prev]; [n[i], n[j]] = [n[j], n[i]]; return n
    })
  }

  async function save() {
    if (!pages.length) return
    setBusy(true)
    try {
      const fd = new FormData()
      pages.forEach((p, i) => fd.append('files', p.blob, `page-${i + 1}.jpg`))
      fd.append('title', title.trim())
      fd.append('category', cat)
      fd.append('expiry_date', expiry)
      fd.append('notes', notes.trim())
      const res = await fetch('/api/documents/scan', {
        method: 'POST', headers: { Authorization: `Bearer ${tokenStore.get()}` }, body: fd,
      })
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Scan failed')
      const { item } = await res.json()
      toast(`Saved · ${pages.length} page${pages.length === 1 ? '' : 's'}`)
      onSaved(item)
    } catch (e) { toast(e instanceof Error ? e.message : 'Could not save the scan') }
    finally { setBusy(false) }
  }

  useEffect(() => () => {
    pages.forEach((p) => { URL.revokeObjectURL(p.url); URL.revokeObjectURL(p.rawUrl) })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  /* ------------------------------------------------------------ render */

  if (step === 'crop' && draft) {
    return (
      <CropScreen
        draft={draft} busy={busy}
        onChange={(d) => setDraft(d)}
        onCancel={() => {
          if (draft.editingId == null) URL.revokeObjectURL(draft.rawUrl)
          setDraft(null)
          setStep(draft.editingId != null ? 'preview' : 'camera')
        }}
        onDone={() => commitDraft(draft)}
      />
    )
  }

  if (step === 'preview' && pages[viewIdx]) {
    const p = pages[viewIdx]
    return (
      <PagePreview
        page={p} index={viewIdx} total={pages.length} busy={busy}
        onBack={() => setStep('pages')}
        onPrev={() => setViewIdx((i) => Math.max(0, i - 1))}
        onNext={() => setViewIdx((i) => Math.min(pages.length - 1, i + 1))}
        onRotate={() => updatePage(p.id, { rotation: ((p.rotation + 90) % 360) as Rotation })}
        onFilter={(f) => updatePage(p.id, { filter: f })}
        onRecrop={() => recrop(p)}
        onDelete={() => removePage(p.id)}
      />
    )
  }

  if (step === 'pages') {
    return (
      <div className="screen scan-manage">
        <TopBar title="Scanned pages" sub={`${pages.length} page${pages.length === 1 ? '' : 's'}`}
          onBack={() => setStep('camera')}
          right={<button className="btn sm" disabled={!pages.length} onClick={() => setStep('details')}>Next</button>} />
        <div className="pg-grid">
          {pages.map((p, i) => (
            <div key={p.id} className="pg-card">
              <button className="pg-shot" onClick={() => { setViewIdx(i); setStep('preview') }}>
                <img src={p.url} alt={`Page ${i + 1}`} />
                <span className="pg-n">{i + 1}</span>
              </button>
              <div className="pg-acts">
                <button onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move earlier">↑</button>
                <button onClick={() => move(i, 1)} disabled={i === pages.length - 1} aria-label="Move later">↓</button>
                <button onClick={() => { setViewIdx(i); setStep('preview') }} aria-label="Preview">⤢</button>
                <button className="danger" onClick={() => removePage(p.id)} aria-label="Delete">✕</button>
              </div>
            </div>
          ))}
          <button className="pg-add" onClick={() => setStep('camera')}>
            <span>＋</span>Add page
          </button>
        </div>
        {busy && <div className="scan-busy"><Spinner /></div>}
      </div>
    )
  }

  if (step === 'details') {
    return (
      <div className="screen scan-manage">
        <TopBar title="Save scan" sub={`${pages.length} page${pages.length === 1 ? '' : 's'}`}
          onBack={() => setStep('pages')} />
        <div className="scan-review">
          {pages.slice(0, 4).map((p, i) => <img key={p.id} src={p.url} alt={`Page ${i + 1}`} />)}
          {pages.length > 4 && <div className="scan-more">+{pages.length - 4}</div>}
        </div>
        <Field label="Title">
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Rental Agreement" autoFocus />
        </Field>
        <Field label="Category">
          <div className="doc-catpick">
            {cats.map((c) => (
              <button key={c.key} type="button" className={`doc-catopt${cat === c.key ? ' on' : ''}`}
                onClick={() => setCat(c.key)}><span>{c.emoji}</span>{c.label}</button>
            ))}
          </div>
        </Field>
        <Field label="Expiry date (optional)">
          <input className="input" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        </Field>
        <Field label="Notes (optional)">
          <textarea className="input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        <button className="btn block" disabled={busy || !pages.length} onClick={save}>
          {busy ? 'Saving…' : `Save as PDF · ${pages.length} page${pages.length === 1 ? '' : 's'}`}
        </button>
      </div>
    )
  }

  /* ---- camera ---- */
  return (
    <div className="scanner">
      <div className="scan-cam">
        <video ref={video} playsInline muted autoPlay />
        <canvas ref={overlay} className="scan-overlay" />
        {!camError && <div className="scan-guides" aria-hidden="true"><i /><i /><i /><i /></div>}
        {camError && (
          <div className="scan-camerr"><div style={{ fontSize: 40 }}>📷</div><p>{camError}</p></div>
        )}

        <div className="scan-top">
          <button className="scan-round" onClick={onClose} aria-label="Cancel">✕</button>
          <div className={`scan-hint${ready ? ' ok' : ''}`}>
            {camError ? 'Import a photo below'
              : ready ? (auto ? 'Hold still — capturing…' : 'Page detected')
                : 'Point at the document'}
          </div>
          {hasTorch
            ? <button className={`scan-round${torch ? ' on' : ''}`} onClick={toggleTorch} aria-label="Torch">⚡</button>
            : <span className="scan-round ghost" />}
        </div>
      </div>

      <input ref={filePick} type="file" accept="image/*" hidden
        onChange={(e) => { importFile(e.target.files); e.currentTarget.value = '' }} />

      <div className="scan-bar">
        <div className="scan-modes">
          <button className={`scan-chip${auto ? ' on' : ''}`} onClick={() => setAuto((a) => !a)}>
            {auto ? '⦿ Auto capture' : '○ Manual'}
          </button>
          <button className="scan-chip" onClick={() => filePick.current?.click()}>🖼 Import</button>
        </div>

        <div className="scan-shutter-row">
          <div className="scan-side">
            {pages.length > 0 && (
              <button className="scan-thumb-btn" onClick={() => setStep('pages')} aria-label="Scanned pages">
                <img src={pages[pages.length - 1].url} alt="" />
                <span>{pages.length}</span>
              </button>
            )}
          </div>
          <button className={`scan-shutter${ready ? ' ready' : ''}`} onClick={capture}
            disabled={!!camError || busy} aria-label="Capture" />
          <div className="scan-side end">
            {pages.length > 0 && (
              <button className="scan-chip solid" onClick={() => setStep('pages')}>Done</button>
            )}
          </div>
        </div>
      </div>

      {busy && <div className="scan-busy"><Spinner /></div>}
    </div>
  )
}

/* ------------------------------------------------------- crop / corners */

function CropScreen({ draft, busy, onChange, onCancel, onDone }: {
  draft: Draft; busy: boolean
  onChange: (d: Draft) => void; onCancel: () => void; onDone: () => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const img = useRef<HTMLImageElement>(null)
  const [drag, setDrag] = useState(-1)
  const [nat, setNat] = useState({ w: draft.w, h: draft.h })

  const layout = () => {
    const el = img.current
    if (!el || !nat.w || !el.clientWidth) return null
    return { left: el.offsetLeft, top: el.offsetTop, sx: el.clientWidth / nat.w, sy: el.clientHeight / nat.h }
  }
  const toScreen = (p: Pt) => {
    const L = layout()
    return L ? { x: L.left + p.x * L.sx, y: L.top + p.y * L.sy } : { x: 0, y: 0 }
  }
  const fromClient = (cx: number, cy: number): Pt | null => {
    const L = layout(); const b = box.current?.getBoundingClientRect()
    if (!L || !b) return null
    return {
      x: Math.max(0, Math.min(nat.w, (cx - b.left - L.left) / L.sx)),
      y: Math.max(0, Math.min(nat.h, (cy - b.top - L.top) / L.sy)),
    }
  }

  useEffect(() => {
    if (drag < 0) return
    const move = (e: PointerEvent) => {
      const p = fromClient(e.clientX, e.clientY)
      if (!p) return
      const q = [...draft.quad] as Quad
      q[drag] = p
      onChange({ ...draft, quad: q })
    }
    const up = () => setDrag(-1)
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('pointerup', up)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
  }, [drag, draft, onChange, nat]) // eslint-disable-line react-hooks/exhaustive-deps

  const pts = draft.quad.map(toScreen)
  const poly = pts.map((p) => `${p.x},${p.y}`).join(' ')
  // Loupe: a zoomed window onto the corner being dragged, so a fingertip doesn't
  // hide the very detail it's trying to line up.
  const L = layout()
  const loupe = drag >= 0 && L ? {
    bg: `${-draft.quad[drag].x * L.sx * 2 + 55}px ${-draft.quad[drag].y * L.sy * 2 + 55}px`,
    size: `${(img.current?.clientWidth || 0) * 2}px ${(img.current?.clientHeight || 0) * 2}px`,
    left: pts[drag].x < 130 && pts[drag].y < 130 ? 'auto' : 12,
    right: pts[drag].x < 130 && pts[drag].y < 130 ? 12 : 'auto',
  } : null

  return (
    <div className="scanner crop">
      <div className="scan-crop" ref={box}>
        <img ref={img} src={draft.rawUrl} alt="Captured page"
          onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })} />
        {nat.w > 0 && (
          <svg className="scan-quad">
            <polygon points={poly} />
            {pts.map((p, i) => (
              <g key={i}>
                <circle className="hit" cx={p.x} cy={p.y} r={22}
                  onPointerDown={(e) => { e.preventDefault(); setDrag(i) }} />
                <circle className={`knob${drag === i ? ' on' : ''}`} cx={p.x} cy={p.y} r={11} />
              </g>
            ))}
          </svg>
        )}
        {loupe && (
          <div className="scan-loupe" style={{
            backgroundImage: `url(${draft.rawUrl})`,
            backgroundSize: loupe.size,
            backgroundPosition: loupe.bg,
            left: loupe.left as number | 'auto',
            right: loupe.right as number | 'auto',
          }}><span /></div>
        )}
      </div>

      <div className="scan-bar">
        <div className="scan-modes">
          {FILTERS.map((f) => (
            <button key={f.key} className={`scan-chip${draft.filter === f.key ? ' on' : ''}`}
              onClick={() => onChange({ ...draft, filter: f.key })}>{f.label}</button>
          ))}
        </div>
        <p className="scan-tip">Drag the handles to the page corners</p>
        <div className="scan-crop-acts">
          <button className="btn ghost" onClick={onCancel} disabled={busy}>
            {draft.editingId != null ? 'Cancel' : 'Retake'}
          </button>
          <button className="btn ghost" disabled={busy}
            onClick={() => onChange({ ...draft, quad: fullQuad(nat.w, nat.h) })}>Full page</button>
          <button className="btn" onClick={onDone} disabled={busy}>
            {busy ? 'Processing…' : draft.editingId != null ? 'Save' : 'Keep'}
          </button>
        </div>
      </div>
      {busy && <div className="scan-busy"><Spinner /></div>}
    </div>
  )
}

/* --------------------------------------------------- full-page preview */

function PagePreview({ page, index, total, busy, onBack, onPrev, onNext, onRotate, onFilter, onRecrop, onDelete }: {
  page: Page; index: number; total: number; busy: boolean
  onBack: () => void; onPrev: () => void; onNext: () => void
  onRotate: () => void; onFilter: (f: Filter) => void; onRecrop: () => void; onDelete: () => void
}) {
  const [confirm, setConfirm] = useState(false)
  return (
    <div className="viewer">
      <div className="viewer-stage">
        <Zoomable fill src={page.url} alt={`Page ${index + 1}`} />
      </div>

      <div className="viewer-top">
        <button className="viewer-btn" onClick={onBack} aria-label="Back">✕</button>
        <div className="viewer-title">
          <div className="vt-main">Page {index + 1} of {total}</div>
          <div className="vt-sub">Pinch or double-tap to zoom</div>
        </div>
        <button className="viewer-btn" onClick={onRotate} disabled={busy} aria-label="Rotate">⟳</button>
        <button className="viewer-btn" onClick={onRecrop} disabled={busy} aria-label="Re-crop">⛶</button>
        <button className="viewer-btn danger" onClick={() => setConfirm(true)} aria-label="Delete">🗑</button>
      </div>

      <div className="viewer-bottom col">
        <div className="pv-filters">
          {FILTERS.map((f) => (
            <button key={f.key} className={`scan-chip${page.filter === f.key ? ' on' : ''}`}
              disabled={busy} onClick={() => onFilter(f.key)}>{f.label}</button>
          ))}
        </div>
        {total > 1 && (
          <div className="pv-nav">
            <button className="scan-chip" onClick={onPrev} disabled={index === 0}>‹ Prev</button>
            <span className="pv-count">{index + 1} / {total}</span>
            <button className="scan-chip" onClick={onNext} disabled={index === total - 1}>Next ›</button>
          </div>
        )}
      </div>

      {confirm && (
        <div className="scan-confirm">
          <div className="scan-confirm-card">
            <div style={{ fontSize: 32 }}>🗑</div>
            <p>Remove page {index + 1} from this scan?</p>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn ghost block" onClick={() => setConfirm(false)}>Cancel</button>
              <button className="btn danger block" onClick={() => { setConfirm(false); onDelete() }}>Remove</button>
            </div>
          </div>
        </div>
      )}
      {busy && <div className="scan-busy"><Spinner /></div>}
    </div>
  )
}
