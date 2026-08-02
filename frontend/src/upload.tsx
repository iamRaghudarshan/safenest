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
      // Belt to the restore-time check's braces. A File handle can also go stale
      // while the queue is running — the photo is deleted, the drive is unplugged,
      // iOS releases the picker's reference — and sending an empty part gets a
      // 422 whose message tells the owner nothing they can act on.
      if (!(item.blob instanceof Blob) || item.blob.size === 0) {
        const gone = new Error('This photo is no longer readable — pick it again')
        ;(gone as Error & { fatal?: boolean }).fatal = true
        throw gone
      }
      const fd = new FormData()
      fd.append('file', item.blob, item.name || 'photo.jpg')
      const res = await fetch(ENDPOINT, { method: 'POST', headers: { Authorization: `Bearer ${tokenStore.get()}` }, body: fd })
      if (res.status === 401) { paused.current = true; throw new Error('Signed out — sign in again') }
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail
        // A validation error comes back as a LIST, so the old code fell through to
        // "Upload failed (422)" — a number, to somebody holding a phone. It means
        // the file part did not arrive, which is worth saying in words.
        const said = typeof detail === 'string' && detail
          ? detail
          : res.status === 422
            ? 'The photo did not reach the app — pick it again'
            : `Upload failed (${res.status})`
        const err = new Error(said)
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
    // How many bytes this batch may copy into IndexedDB before it stops.
    //
    // Persisting every blob up front is what breaks a big selection from a phone.
    // 400 photos is well over a gigabyte, and the loop below used to read all of
    // it into IndexedDB before a single upload started -- on iOS that exhausts the
    // tab's memory and Safari discards the page, so only the handful already sent
    // arrive. Past this budget the file is queued from its File handle instead,
    // which costs nothing: the bytes are read when it is that file's turn.
    //
    // What is lost beyond the budget is only resumption after a page RELOAD. The
    // queue itself survives navigation either way, and finishing 400 photos beats
    // being able to resume 40.
    let budget = 250 * 1024 * 1024
    // A budget was not enough. Copying blobs at all is what breaks a phone
    // backup, and 250 MB of HEIC is only sixty or so photos — so a 400-photo
    // selection still spent its first, most fragile minute copying bytes nobody
    // asked for, on the one device least able to spare the memory.
    //
    // Persisting exists for ONE thing: resuming after the page is RELOADED. The
    // queue already survives navigation without it. For a big selection that
    // trade is the wrong way round — finishing 400 photos matters more than being
    // able to resume 60 — so a big batch is queued from its File handles only,
    // which costs nothing until each file's turn comes.
    const persist = files.length <= 30
    let seen = 0
    for (const f of Array.from(files)) {
      // Yield every so often, and start sending what is already queued. A
      // synchronous pass over hundreds of files freezes the page — and on a phone
      // a frozen page is one the system may kill — while waiting for the whole
      // selection before uploading anything means a long silence and nothing to
      // show for it if the tab dies partway.
      if (++seen % 25 === 0) {
        bump()
        pumpRef.current()
        await new Promise((r) => setTimeout(r, 0))
      }
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
      if (persist && budget - f.size >= 0) {
        budget -= f.size
        try { item.key = await uploadDB.addFile({ blob: f, name: f.name, size: f.size, sig }) }
        catch { item.key = -1 /* quota exceeded: in-memory only */ }
      }
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
        // The stored row can outlive the bytes it points at. iOS in particular
        // reclaims IndexedDB blob storage under pressure and leaves the record
        // behind, so this comes back as undefined or a zero-length Blob.
        //
        // Queuing it anyway posted a body with nothing in the file part, the
        // server answered 422, and because a 4xx is treated as final the item sat
        // there failed for ever — reappearing on every launch, with a Retry
        // button that could never do anything. Reported as "upload failed (422)".
        if (!(p.blob instanceof Blob) || p.blob.size === 0) {
          uploadDB.deleteFile(p.id!).catch(() => {})
          continue
        }
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
