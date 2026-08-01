// Turning a captured frame into a finished page.
//
// A page keeps its ORIGINAL frame plus the corners, rotation and filter chosen for
// it, and the finished image is regenerated from those whenever any of them change.
// That is what makes a page re-editable after the fact — re-cropping or switching
// filter three pages later costs nothing and never compounds JPEG artefacts, because
// every render starts from the original pixels.
import { outputSize, warp, type Quad } from './geometry'

export type Filter = 'original' | 'grey' | 'bw'
export type Rotation = 0 | 90 | 180 | 270

export const FILTERS: { key: Filter; label: string }[] = [
  { key: 'original', label: 'Colour' },
  { key: 'grey', label: 'Greyscale' },
  { key: 'bw', label: 'Sharpen' },
]

export const MAX_EDGE = 1800

/** Tone clean-up. A percentile stretch adapts to the lighting of the actual shot
 *  instead of assuming an evenly lit page. */
export function enhance(img: ImageData, filter: Filter): ImageData {
  if (filter === 'original') return img
  const px = img.data
  const hist = new Uint32Array(256)
  for (let i = 0; i < px.length; i += 4) {
    const y = (px[i] * 0.299 + px[i + 1] * 0.587 + px[i + 2] * 0.114) | 0
    px[i] = px[i + 1] = px[i + 2] = y
    hist[y]++
  }
  const total = img.width * img.height
  let lo = 0, hi = 255, seen = 0
  for (let v = 0; v < 256; v++) { seen += hist[v]; if (seen > total * 0.02) { lo = v; break } }
  seen = 0
  for (let v = 255; v >= 0; v--) { seen += hist[v]; if (seen > total * 0.10) { hi = v; break } }
  const span = Math.max(1, hi - lo)
  const cut = lo + span * 0.55
  for (let i = 0; i < px.length; i += 4) {
    const y = px[i]
    // 'bw' uses a soft threshold — a hard one shreds thin strokes and signatures.
    const out = filter === 'bw'
      ? (y <= cut ? Math.max(0, ((y - lo) / Math.max(1, cut - lo)) * 90) : 255)
      : Math.max(0, Math.min(255, ((y - lo) / span) * 255))
    px[i] = px[i + 1] = px[i + 2] = out
  }
  return img
}

function draw(img: ImageData): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = img.width; c.height = img.height
  c.getContext('2d')!.putImageData(img, 0, 0)
  return c
}

function rotateCanvas(src: HTMLCanvasElement, deg: Rotation): HTMLCanvasElement {
  if (!deg) return src
  const swap = deg === 90 || deg === 270
  const c = document.createElement('canvas')
  c.width = swap ? src.height : src.width
  c.height = swap ? src.width : src.height
  const g = c.getContext('2d')!
  g.translate(c.width / 2, c.height / 2)
  g.rotate((deg * Math.PI) / 180)
  g.drawImage(src, -src.width / 2, -src.height / 2)
  return c
}

export function blobToImageData(blob: Blob): Promise<ImageData> {
  return createImageBitmap(blob).then((bmp) => {
    const c = document.createElement('canvas')
    c.width = bmp.width; c.height = bmp.height
    const g = c.getContext('2d', { willReadFrequently: true })!
    g.drawImage(bmp, 0, 0)
    bmp.close?.()
    return g.getImageData(0, 0, c.width, c.height)
  })
}

export function canvasToBlob(c: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((res) => c.toBlob((b) => res(b!), 'image/jpeg', quality))
}

/** Full render: original frame -> dewarp -> rotate -> tone -> JPEG. */
export async function renderPage(
  raw: Blob, quad: Quad, rotation: Rotation, filter: Filter,
): Promise<{ blob: Blob; url: string; w: number; h: number }> {
  const src = await blobToImageData(raw)
  const { w, h } = outputSize(quad, MAX_EDGE)
  const flat = enhance(warp(src, quad, w, h), filter)
  const canvas = rotateCanvas(draw(flat), rotation)
  const blob = await canvasToBlob(canvas, filter === 'bw' ? 0.8 : 0.86)
  return { blob, url: URL.createObjectURL(blob), w: canvas.width, h: canvas.height }
}

export const fullQuad = (w: number, h: number): Quad =>
  [{ x: 0, y: 0 }, { x: w, y: 0 }, { x: w, y: h }, { x: 0, y: h }]
