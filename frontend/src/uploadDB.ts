// IndexedDB persistence for the upload queue — lets a bulk upload resume after a
// full page reload. Stores pending file blobs + a set of already-uploaded
// signatures (for dedup so a resumed/re-selected batch never re-uploads).
const DB_NAME = 'finmate-uploads'
const VERSION = 1

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains('files')) db.createObjectStore('files', { keyPath: 'id', autoIncrement: true })
      if (!db.objectStoreNames.contains('sigs')) db.createObjectStore('sigs', { keyPath: 'sig' })
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function run<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest): Promise<T> {
  return openDB().then((db) => new Promise<T>((resolve, reject) => {
    const t = db.transaction(store, mode)
    const req = fn(t.objectStore(store))
    req.onsuccess = () => resolve(req.result as T)
    req.onerror = () => reject(req.error)
    t.oncomplete = () => db.close()
  }))
}

export interface QueuedFile { id?: number; blob: Blob; name: string; size: number; sig: string }

export const uploadDB = {
  addFile: (rec: Omit<QueuedFile, 'id'>) => run<number>('files', 'readwrite', (s) => s.add(rec)),
  allFiles: () => run<QueuedFile[]>('files', 'readonly', (s) => s.getAll()),
  deleteFile: (id: number) => run<void>('files', 'readwrite', (s) => s.delete(id)),
  clearFiles: () => run<void>('files', 'readwrite', (s) => s.clear()),
  addSig: (sig: string) => run<void>('sigs', 'readwrite', (s) => s.put({ sig })).catch(() => {}),
  allSigs: async () => {
    const rows = await run<{ sig: string }[]>('sigs', 'readonly', (s) => s.getAll()).catch(() => [])
    return new Set(rows.map((r) => r.sig))
  },
  // Wipe everything on sign-out — queued blobs are the user's own photos.
  clearAll: async () => {
    await run<void>('files', 'readwrite', (s) => s.clear()).catch(() => {})
    await run<void>('sigs', 'readwrite', (s) => s.clear()).catch(() => {})
  },
}
