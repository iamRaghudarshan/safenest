// Resilient background upload manager. Holds a queue in memory (survives in-app
// navigation), uploads with limited concurrency, auto-retries with backoff,
// pauses when offline and resumes on reconnect, and persists pending blobs to
// IndexedDB so a full page reload continues from where it left off. Dedupes by
// file signature so nothing is uploaded twice.
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { tokenStore } from './api'
import { uploadDB } from './uploadDB'

const CONCURRENCY = 4
const MAX_TRIES = 6
const ENDPOINT = '/api/gallery/upload?faces=0' // skip slow face detection during bulk

type Status = 'pending' | 'uploading' | 'error'
interface Item {
  key: number; blob: Blob; name: string; sig: string; tries: number; status: Status
  /** Why it failed, in words. Without this the UI can only say "failed". */
  reason?: string
}

interface UploadState {
  total: number; done: number; failed: number; dupes: number; pending: number; active: number
  uploading: boolean; paused: boolean; offline: boolean
  enqueue: (files: FileList | File[]) => Promise<number>
  /** Distinct reasons the failed uploads gave, for showing the user. */
  reasons: string[]
  pause: () => void; resume: () => void; cancelAll: () => void; retryFailed: () => void
  onBatchDone: (cb: () => void) => () => void
}

const Ctx = createContext<UploadState>(null!)
const sigOf = (f: File) => `${f.name}|${f.size}|${f.lastModified || 0}`

export function UploadProvider({ children }: { children: ReactNode }) {
  const items = useRef<Item[]>([])
  const doneSigs = useRef<Set<string>>(new Set())
  const active = useRef(0)
  const paused = useRef(false)
  const counts = useRef({ total: 0, done: 0, failed: 0, dupes: 0 })
  const batchCbs = useRef<Set<() => void>>(new Set())
  const pumpRef = useRef<() => void>(() => {})
  const [, force] = useState(0)
  const [offline, setOffline] = useState(typeof navigator !== 'undefined' && navigator.onLine === false)
  const bump = () => force((n) => (n + 1) & 0xffff)

  async function uploadOne(item: Item) {
    try {
      const fd = new FormData()
      fd.append('file', item.blob, item.name)
      const res = await fetch(ENDPOINT, { method: 'POST', headers: { Authorization: `Bearer ${tokenStore.get()}` }, body: fd })
      if (res.status === 401) { paused.current = true; throw new Error('Signed out — sign in again') }
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail
        const err = new Error(typeof detail === 'string' && detail ? detail : `Upload failed (${res.status})`)
        // A 4xx means the server has judged this file and will judge it the same
        // way every time. Retrying six times with backoff just makes a certain
        // failure take half a minute. Only network faults and 5xx are worth a retry.
        if (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 429) {
          ;(err as Error & { fatal?: boolean }).fatal = true
        }
        throw err
      }
      const out = await res.json().catch(() => ({}))
      counts.current.done++
      if (out && out.duplicate) counts.current.dupes++ // server already had this image
      doneSigs.current.add(item.sig)
      uploadDB.addSig(item.sig)
      if (item.key >= 0) uploadDB.deleteFile(item.key).catch(() => {})
      items.current = items.current.filter((i) => i !== item)
    } catch (e) {
      const err = e as Error & { fatal?: boolean }
      item.reason = err.message || 'Upload failed'
      item.tries++
      if (err.fatal || item.tries >= MAX_TRIES) {
        item.status = 'error'; counts.current.failed++
      } else {
        item.status = 'pending'
        await new Promise((r) => setTimeout(r, Math.min(1500 * item.tries, 6000)))
      }
    } finally {
      active.current--
      bump()
      pumpRef.current()
    }
  }

  function pump() {
    if (paused.current || (typeof navigator !== 'undefined' && navigator.onLine === false)) { bump(); return }
    while (active.current < CONCURRENCY) {
      const next = items.current.find((i) => i.status === 'pending')
      if (!next) break
      next.status = 'uploading'
      active.current++
      uploadOne(next)
    }
    bump()
    if (active.current === 0 && !items.current.some((i) => i.status === 'pending') && counts.current.total > 0) {
      batchCbs.current.forEach((cb) => cb())
    }
  }
  pumpRef.current = pump

  async function enqueue(files: FileList | File[]) {
    let added = 0
    for (const f of Array.from(files)) {
      const sig = sigOf(f)
      if (doneSigs.current.has(sig) || items.current.some((i) => i.sig === sig)) continue
      // A zero-byte read is the browser saying it could not get at the file, not
      // that the file is empty. On a Mac that is almost always iCloud Drive with
      // "Optimise Storage" on: the photo is listed, but its bytes are still in the
      // cloud. Sending nothing and letting the server reject it tells the person
      // only that it "failed".
      if (f.size === 0) {
        items.current.push({
          key: -1, blob: f, name: f.name, sig, tries: MAX_TRIES, status: 'error',
          reason: 'This file reads as empty — if it is in iCloud, open it once so it downloads',
        })
        counts.current.total++; counts.current.failed++; added++
        continue
      }
      const item: Item = { key: -1, blob: f, name: f.name, sig, tries: 0, status: 'pending' }
      items.current.push(item)
      counts.current.total++
      added++
      try { item.key = await uploadDB.addFile({ blob: f, name: f.name, size: f.size, sig }) }
      catch { item.key = -1 /* quota exceeded: in-memory only */ }
    }
    bump()
    pumpRef.current()
    return added
  }

  function cancelAll() {
    items.current = items.current.filter((i) => i.status === 'uploading') // let in-flight finish
    counts.current = { total: 0, done: 0, failed: 0, dupes: 0 }
    uploadDB.clearFiles().catch(() => {})
    bump()
  }
  function retryFailed() {
    items.current.forEach((i) => { if (i.status === 'error') { i.status = 'pending'; i.tries = 0; counts.current.failed-- } })
    bump()
    pumpRef.current()
  }

  useEffect(() => {
    let alive = true
    ;(async () => {
      doneSigs.current = await uploadDB.allSigs()
      const persisted = await uploadDB.allFiles().catch(() => [])
      if (!alive || !persisted.length) return
      for (const p of persisted) {
        items.current.push({ key: p.id!, blob: p.blob, name: p.name, sig: p.sig, tries: 0, status: 'pending' })
        counts.current.total++
      }
      bump()
      pumpRef.current()
    })()
    const on = () => { setOffline(false); pumpRef.current() }
    const off = () => { setOffline(true); bump() }
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => { alive = false; window.removeEventListener('online', on); window.removeEventListener('offline', off) }
  }, [])

  const pending = items.current.filter((i) => i.status === 'pending').length
  const activeN = items.current.filter((i) => i.status === 'uploading').length
  const c = counts.current
  const value: UploadState = {
    total: c.total, done: c.done, failed: c.failed, dupes: c.dupes, pending, active: activeN,
    uploading: pending > 0 || activeN > 0, paused: paused.current, offline,
    enqueue,
    reasons: [...new Set(items.current.filter((i) => i.status === 'error' && i.reason)
      .map((i) => i.reason as string))],
    pause: () => { paused.current = true; bump() }, resume: () => { paused.current = false; bump(); pumpRef.current() },
    cancelAll, retryFailed,
    onBatchDone: (cb) => { batchCbs.current.add(cb); return () => { batchCbs.current.delete(cb) } },
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useUpload = () => useContext(Ctx)
