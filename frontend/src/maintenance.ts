// Device-side cache maintenance.
//
// A PWA holds three separate stores: the service worker's CacheStorage (app shell
// + API responses), IndexedDB (the pending upload queue), and the worker
// registration itself. When a bad build or a stale CDN copy gets latched, clearing
// all three is the reliable way out — and it's something the user should be able to
// do from inside the app rather than through browser settings.
import { uploadDB } from './uploadDB'

declare const __BUILD_ID__: string
export const BUILD_ID = typeof __BUILD_ID__ === 'string' ? __BUILD_ID__ : 'dev'

export interface StorageInfo { usedBytes: number; caches: number; hasWorker: boolean }

export async function storageInfo(): Promise<StorageInfo> {
  let usedBytes = 0
  try { usedBytes = (await navigator.storage?.estimate?.())?.usage ?? 0 } catch { /* unsupported */ }
  let count = 0
  try { count = 'caches' in window ? (await caches.keys()).length : 0 } catch { /* unsupported */ }
  let hasWorker = false
  try { hasWorker = !!(await navigator.serviceWorker?.getRegistration?.()) } catch { /* unsupported */ }
  return { usedBytes, caches: count, hasWorker }
}

export const formatBytes = (n: number): string =>
  n >= 1073741824 ? `${(n / 1073741824).toFixed(1)} GB`
    : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB`
      : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`

/** Ask the browser to re-check for a new build. Resolves true if one was found. */
export async function checkForUpdate(): Promise<boolean> {
  if (!('serviceWorker' in navigator)) return false
  const reg = await navigator.serviceWorker.getRegistration()
  if (!reg) return false
  await reg.update()
  // `installing` or `waiting` means a different worker script was downloaded.
  const pending = reg.installing || reg.waiting
  if (pending) {
    pending.postMessage('skip-waiting')
    // Reload HERE rather than leaving it to the controllerchange handler in
    // main.tsx: that one deliberately defers until the app is next brought to the
    // foreground, so as not to yank the page mid-task. But this path only runs
    // because the user explicitly asked for the update, so applying it straight
    // away is exactly what they want — otherwise the new build downloads and
    // silently never appears.
    window.setTimeout(() => location.reload(), 500)
    return true
  }
  return false
}

/**
 * Wipe every cached copy of the app and its data, then let the caller reload.
 * Deliberately leaves localStorage alone so the user stays signed in — this is a
 * cache reset, not a sign-out.
 */
export async function clearAppCache(): Promise<void> {
  try {
    if ('caches' in window) {
      const keys = await caches.keys()
      await Promise.all(keys.map((k) => caches.delete(k)))
    }
  } catch { /* best effort */ }

  try { await uploadDB.clearAll() } catch { /* best effort */ }

  try {
    const regs = (await navigator.serviceWorker?.getRegistrations?.()) || []
    await Promise.all(regs.map((r) => r.unregister()))
  } catch { /* best effort */ }
}

