// Projective geometry for document scanning: fit a homography to the four detected
// page corners, then resample the photo through it so a page shot at an angle comes
// out flat and rectangular — the "dewarp" step that makes a scan look like a scan
// rather than a snapshot.
//
// The 2D canvas API can only do affine transforms (setTransform takes 6 values), so
// the projective warp is done by hand: invert the homography once, then for every
// output pixel map back into the source and sample bilinearly.

export interface Pt { x: number; y: number }
export type Quad = [Pt, Pt, Pt, Pt] // clockwise from top-left

/** Solve the 8x8 system for the homography taking `src` to `dst`. */
export function homography(src: Quad, dst: Quad): number[] {
  // Each correspondence contributes two rows; the 9th element is fixed at 1.
  const A: number[][] = []
  const b: number[] = []
  for (let i = 0; i < 4; i++) {
    const { x, y } = src[i]
    const { x: u, y: v } = dst[i]
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u)
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v)
  }
  const h = solve(A, b)
  return [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1]
}

/** Gaussian elimination with partial pivoting. */
function solve(A: number[][], b: number[]): number[] {
  const n = b.length
  const M = A.map((row, i) => [...row, b[i]])
  for (let col = 0; col < n; col++) {
    let piv = col
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r
    ;[M[col], M[piv]] = [M[piv], M[col]]
    const d = M[col][col] || 1e-12
    for (let r = 0; r < n; r++) {
      if (r === col) continue
      const f = M[r][col] / d
      if (!f) continue
      for (let c = col; c <= n; c++) M[r][c] -= f * M[col][c]
    }
  }
  // Fully reduced: each row is pivot * x_i = rhs.
  return M.map((row, i) => row[n] / (M[i][i] || 1e-12))
}

export function invert3(m: number[]): number[] {
  const [a, b, c, d, e, f, g, h, i] = m
  const A = e * i - f * h, B = f * g - d * i, C = d * h - e * g
  const det = a * A + b * B + c * C || 1e-12
  return [
    A / det, (c * h - b * i) / det, (b * f - c * e) / det,
    B / det, (a * i - c * g) / det, (c * d - a * f) / det,
    C / det, (b * g - a * h) / det, (a * e - b * d) / det,
  ]
}

const dist = (p: Pt, q: Pt) => Math.hypot(p.x - q.x, p.y - q.y)

/**
 * Output size for a warped quad: use the longer of each opposing edge pair so no
 * part of the page is squeezed, then clamp the long side to `maxEdge`.
 */
export function outputSize(q: Quad, maxEdge = 1800): { w: number; h: number } {
  let w = Math.max(dist(q[0], q[1]), dist(q[3], q[2]))
  let h = Math.max(dist(q[0], q[3]), dist(q[1], q[2]))
  w = Math.max(32, Math.round(w))
  h = Math.max(32, Math.round(h))
  const scale = Math.min(1, maxEdge / Math.max(w, h))
  return { w: Math.round(w * scale), h: Math.round(h * scale) }
}

/**
 * Resample `src` through the quad into a flat w x h image.
 * Bilinear sampling keeps small text legible; nearest-neighbour would alias it.
 */
export function warp(src: ImageData, quad: Quad, w: number, h: number): ImageData {
  const dst: Quad = [{ x: 0, y: 0 }, { x: w, y: 0 }, { x: w, y: h }, { x: 0, y: h }]
  const Hi = invert3(homography(quad, dst))
  const out = new ImageData(w, h)
  const s = src.data, o = out.data
  const sw = src.width, sh = src.height

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // Map the output pixel centre back into the source image.
      const cx = x + 0.5, cy = y + 0.5
      const den = Hi[6] * cx + Hi[7] * cy + Hi[8]
      const sx = (Hi[0] * cx + Hi[1] * cy + Hi[2]) / den
      const sy = (Hi[3] * cx + Hi[4] * cy + Hi[5]) / den
      const di = (y * w + x) * 4

      if (sx < 0 || sy < 0 || sx >= sw - 1 || sy >= sh - 1) {
        o[di] = o[di + 1] = o[di + 2] = 255; o[di + 3] = 255
        continue
      }
      const x0 = sx | 0, y0 = sy | 0
      const fx = sx - x0, fy = sy - y0
      const i00 = (y0 * sw + x0) * 4, i10 = i00 + 4
      const i01 = i00 + sw * 4, i11 = i01 + 4
      const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy)
      const w01 = (1 - fx) * fy, w11 = fx * fy
      for (let c = 0; c < 3; c++) {
        o[di + c] = s[i00 + c] * w00 + s[i10 + c] * w10 + s[i01 + c] * w01 + s[i11 + c] * w11
      }
      o[di + 3] = 255
    }
  }
  return out
}

/** Order four unsorted points clockwise starting from the top-left. */
export function orderQuad(pts: Pt[]): Quad {
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length
  const sorted = [...pts].sort((a, b) =>
    Math.atan2(a.y - cy, a.x - cx) - Math.atan2(b.y - cy, b.x - cx))
  // Rotate so the point closest to the top-left corner comes first.
  let start = 0, best = Infinity
  sorted.forEach((p, i) => {
    const d = p.x + p.y
    if (d < best) { best = d; start = i }
  })
  return [sorted[start], sorted[(start + 1) % 4], sorted[(start + 2) % 4], sorted[(start + 3) % 4]] as Quad
}
