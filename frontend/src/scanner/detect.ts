// Real-time document-edge detection.
//
// Runs on a heavily downscaled copy of each camera frame (~240px wide) so it can
// keep up at video rate on a phone without OpenCV or a WASM blob — the page stays
// dependency-free and works offline.
//
// Pipeline: grayscale -> box blur -> Otsu threshold -> largest connected component
// -> extreme points of that component as the four page corners. Documents are
// almost always the brightest large region against a darker surface, which makes
// this far more robust in practice than edge-linking or a Hough transform, and it
// costs a fraction of the time.
import type { Pt, Quad } from './geometry'
import { orderQuad } from './geometry'

export const DETECT_W = 240

export interface Detection {
  quad: Quad          // in DETECT_W-space coordinates
  coverage: number    // fraction of the frame the page fills
  confident: boolean  // passed the shape/size sanity checks
}

/** Downscale a frame into a small working canvas and return its pixels. */
export function sample(source: CanvasImageSource, sw: number, sh: number,
                       ctx: CanvasRenderingContext2D): ImageData {
  const w = DETECT_W
  const h = Math.max(1, Math.round((sh / sw) * w))
  ctx.canvas.width = w
  ctx.canvas.height = h
  ctx.drawImage(source, 0, 0, w, h)
  return ctx.getImageData(0, 0, w, h)
}

function grayBlur(img: ImageData): Uint8ClampedArray {
  const { width: w, height: h, data } = img
  const g = new Uint8ClampedArray(w * h)
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    g[p] = (data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114) | 0
  }
  // Separable 3x3 box blur — enough to stop paper texture and JPEG noise from
  // fragmenting the page into many small components.
  const t = new Uint8ClampedArray(w * h)
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const a = g[y * w + Math.max(0, x - 1)], b = g[y * w + x], c = g[y * w + Math.min(w - 1, x + 1)]
      t[y * w + x] = (a + b + c) / 3
    }
  }
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const a = t[Math.max(0, y - 1) * w + x], b = t[y * w + x], c = t[Math.min(h - 1, y + 1) * w + x]
      g[y * w + x] = (a + b + c) / 3
    }
  }
  return g
}

/** Otsu's method: the threshold that best separates the histogram into two classes. */
function otsu(g: Uint8ClampedArray): number {
  const hist = new Uint32Array(256)
  for (let i = 0; i < g.length; i++) hist[g[i]]++
  const total = g.length
  let sum = 0
  for (let i = 0; i < 256; i++) sum += i * hist[i]
  let sumB = 0, wB = 0, best = 0, thr = 128
  for (let t = 0; t < 256; t++) {
    wB += hist[t]
    if (!wB) continue
    const wF = total - wB
    if (!wF) break
    sumB += t * hist[t]
    const mB = sumB / wB, mF = (sum - sumB) / wF
    const between = wB * wF * (mB - mF) * (mB - mF)
    if (between > best) { best = between; thr = t }
  }
  return thr
}

/**
 * Label every connected region of "paper" pixels and return the id of the biggest,
 * alongside the label map. Iterative flood fill — recursion would blow the stack on
 * a region covering most of the frame.
 */
function largestComponent(mask: Uint8Array, w: number, h: number):
  { labels: Int32Array; id: number; size: number } | null {
  const labels = new Int32Array(w * h) // 0 = unvisited/background
  const stack: number[] = []
  let id = 0, bestId = 0, bestSize = 0

  for (let start = 0; start < mask.length; start++) {
    if (!mask[start] || labels[start]) continue
    id++
    let size = 0
    stack.length = 0
    stack.push(start)
    labels[start] = id
    while (stack.length) {
      const p = stack.pop()!
      size++
      const x = p % w, y = (p / w) | 0
      if (x > 0 && mask[p - 1] && !labels[p - 1]) { labels[p - 1] = id; stack.push(p - 1) }
      if (x < w - 1 && mask[p + 1] && !labels[p + 1]) { labels[p + 1] = id; stack.push(p + 1) }
      if (y > 0 && mask[p - w] && !labels[p - w]) { labels[p - w] = id; stack.push(p - w) }
      if (y < h - 1 && mask[p + w] && !labels[p + w]) { labels[p + w] = id; stack.push(p + w) }
    }
    if (size > bestSize) { bestSize = size; bestId = id }
  }
  return bestId ? { labels, id: bestId, size: bestSize } : null
}

/**
 * Corners of a (roughly rectangular) region via extreme points: the page corner
 * nearest each image corner is the one minimising/maximising x±y. Cheap, and
 * stable for a rotated rectangle — which is exactly what a photographed page is.
 */
function cornersOf(labels: Int32Array, id: number, w: number, h: number): Quad | null {
  let tl = Infinity, br = -Infinity, tr = -Infinity, bl = Infinity
  let pTL: Pt | null = null, pBR: Pt | null = null, pTR: Pt | null = null, pBL: Pt | null = null
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (labels[y * w + x] !== id) continue
      const s = x + y, d = x - y
      if (s < tl) { tl = s; pTL = { x, y } }
      if (s > br) { br = s; pBR = { x, y } }
      if (d > tr) { tr = d; pTR = { x, y } }
      if (d < bl) { bl = d; pBL = { x, y } }
    }
  }
  if (!pTL || !pTR || !pBR || !pBL) return null
  return orderQuad([pTL, pTR, pBR, pBL])
}

const cross = (o: Pt, a: Pt, b: Pt) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)

function quadArea(q: Quad): number {
  let a = 0
  for (let i = 0; i < 4; i++) {
    const p = q[i], n = q[(i + 1) % 4]
    a += p.x * n.y - n.x * p.y
  }
  return Math.abs(a) / 2
}

/** Reject shapes that aren't a plausible page: too small, too skewed, or concave. */
function plausible(q: Quad, w: number, h: number): boolean {
  const area = quadArea(q)
  const cov = area / (w * h)
  if (cov < 0.12 || cov > 0.995) return false
  // Convexity: every turn must have the same sign.
  let sign = 0
  for (let i = 0; i < 4; i++) {
    const c = cross(q[i], q[(i + 1) % 4], q[(i + 2) % 4])
    const s = Math.sign(c)
    if (!s) continue
    if (!sign) sign = s
    else if (s !== sign) return false
  }
  // Opposing edges shouldn't differ wildly — that means a bad corner, not perspective.
  const len = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y)
  const top = len(q[0], q[1]), bottom = len(q[3], q[2])
  const left = len(q[0], q[3]), right = len(q[1], q[2])
  const ratio = (a: number, b: number) => Math.min(a, b) / Math.max(a, b || 1)
  if (ratio(top, bottom) < 0.45 || ratio(left, right) < 0.45) return false
  if (Math.min(top, bottom, left, right) < 20) return false
  return true
}

/** Find the page in one downscaled frame. */
export function detect(img: ImageData): Detection | null {
  const { width: w, height: h } = img
  const g = grayBlur(img)
  const thr = otsu(g)

  // Paper is the brighter class. Nudge the threshold down slightly so a soft
  // shadow along one edge doesn't eat into the page.
  const cut = Math.max(0, thr - 6)
  const mask = new Uint8Array(w * h)
  for (let i = 0; i < g.length; i++) mask[i] = g[i] > cut ? 1 : 0

  const comp = largestComponent(mask, w, h)
  if (!comp) return null
  const quad = cornersOf(comp.labels, comp.id, w, h)
  if (!quad) return null

  const coverage = quadArea(quad) / (w * h)
  return { quad, coverage, confident: plausible(quad, w, h) }
}

/** Scale a quad from detector space into the full-resolution frame. */
export function scaleQuad(q: Quad, from: number, to: number): Quad {
  const k = to / from
  return q.map((p) => ({ x: p.x * k, y: p.y * k })) as Quad
}

/** How far apart two quads are, in pixels — used to tell when framing has settled. */
export function quadDrift(a: Quad, b: Quad): number {
  let d = 0
  for (let i = 0; i < 4; i++) d = Math.max(d, Math.hypot(a[i].x - b[i].x, a[i].y - b[i].y))
  return d
}
