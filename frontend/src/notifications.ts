// Web-push enrolment for the daily digest.
//
// iOS only permits this for a PWA installed to the home screen (iOS 16.4+) — in a
// normal Safari tab the APIs are absent, so we detect that and say so plainly
// rather than showing a switch that silently does nothing.
import { api } from './api'
import { SW_URL, SW_OPTIONS } from './swUrl'
import { appName } from './branding'

export interface PushSettings {
  available: boolean          // server has VAPID keys configured
  publicKey: string | null
  enabled: boolean
  sendHour: number
  sendMinute: number
  includeBills: boolean
  includeReminders: boolean
  includeExpiry: boolean
  devices: number
  lastSentOn: string | null
}

/** Whether this browser can receive push at all. */
export function pushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

/** True when running as an installed PWA — the only place iOS allows push. */
export function isStandalone(): boolean {
  return window.matchMedia('(display-mode: standalone)').matches
    || (navigator as unknown as { standalone?: boolean }).standalone === true
}

export function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

/** Why push can't be turned on here, or '' when it can. */
export function blockedReason(): string {
  // Before blaming the browser. On an insecure origin the push APIs are simply
  // absent, so the old message told people their browser was at fault when the
  // real answer was the address they had opened — and the address the app itself
  // suggests for phones (http://192.168.x.x:8080) is exactly one of those.
  if (!window.isSecureContext) {
    return `Notifications need a secure connection, and ${location.protocol}//${location.host} is not one. `
      + `Open ${appName()} at http://127.0.0.1 on the computer it runs on, or set up your web address to reach it securely from anywhere.`
  }
  if (!pushSupported()) {
    return isIOS() && !isStandalone()
      ? `On iPhone, notifications work only from the installed app. Tap Share → Add to Home Screen, then open ${appName()} from that icon.`
      : 'This browser doesn’t support notifications.'
  }
  if (Notification.permission === 'denied') {
    return 'Notifications are blocked for this site in your browser settings. Allow them there, then try again.'
  }
  return ''
}

// VAPID keys travel as base64url; PushManager wants raw bytes.
function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)))
}

export const getSettings = () => api<PushSettings>('/api/notifications/settings')

export const saveSettings = (patch: Partial<PushSettings>) =>
  api<PushSettings>('/api/notifications/settings', { method: 'PUT', body: patch })

/** Ask permission, subscribe this device, and register it with the server. */
export async function enable(publicKey: string): Promise<PushSettings> {
  // Checked before anything else, because every failure below looks the same from
  // the outside. Browsers expose push only on a secure origin, and the address
  // this app tells people to use on their phone (http://192.168.x.x:8080) is not
  // one — so the switch was dead there for a reason nothing on screen explained.
  if (!window.isSecureContext) {
    throw new Error(
      'Notifications need a secure connection. They work on this computer at '
      + 'http://127.0.0.1, or from anywhere once you have set up your web address.')
  }
  if (!publicKey) {
    throw new Error('This copy has no notification key, so alerts cannot be sent.')
  }
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('Notification permission was not granted')

  const reg = await readyRegistration()
  // Reuse an existing subscription if the browser already made one; creating a
  // second with a different key would fail.
  let sub = await reg.pushManager.getSubscription()
  if (sub) {
    const current = new Uint8Array(sub.options.applicationServerKey || new ArrayBuffer(0))
    const wanted = urlBase64ToUint8Array(publicKey)
    const same = current.length === wanted.length && current.every((b, i) => b === wanted[i])
    if (!same) { await sub.unsubscribe(); sub = null }
  }
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
    })
  }
  return api<PushSettings>('/api/notifications/subscribe', { method: 'POST', body: sub.toJSON() })
}

/** Unsubscribe this device (leaves other devices alone). */
export async function disable(): Promise<PushSettings> {
  let endpoint = ''
  try {
    // readyRegistration(), not the bare `ready`: turning notifications OFF must
    // not be able to hang either. A pending promise never reaches the catch.
    const reg = await readyRegistration()
    const sub = await reg.pushManager.getSubscription()
    if (sub) { endpoint = sub.endpoint; await sub.unsubscribe() }
  } catch { /* still tell the server to forget it */ }
  return api<PushSettings>('/api/notifications/unsubscribe', { method: 'POST', body: { endpoint } })
}

export interface DeviceState {
  /** '' when this device is genuinely wired up; otherwise why it isn't. */
  problem: string
  subscribed: boolean
  repaired: boolean
}

/** navigator.serviceWorker.ready never settles when nothing is registered, which
 *  would hang the repair forever. Register on demand, and give up loudly. */
async function readyRegistration(): Promise<ServiceWorkerRegistration> {
  if (!(await navigator.serviceWorker.getRegistration())) {
    await navigator.serviceWorker.register(SW_URL, SW_OPTIONS)
  }
  return await Promise.race([
    navigator.serviceWorker.ready,
    new Promise<never>((_, reject) => setTimeout(
      () => reject(new Error(`The background service did not start. Close ${appName()} `
                             + 'completely and reopen it, then try again.')), 10_000)),
  ])
}

/**
 * Reconcile this device's real push subscription with the server's record.
 *
 * These drift apart silently and often: clearing the app cache unregisters the
 * service worker (which discards the subscription), reinstalling the home-screen
 * app makes a new one, and iOS may drop it on its own. The server keeps the stale
 * row, the push service keeps returning 201, and nothing ever arrives — with no
 * error anywhere to explain it.
 *
 * Run on startup. If permission is already granted, re-subscribing needs no prompt,
 * so the repair is invisible.
 */
export async function syncSubscription(
  publicKey: string | null,
  opts: { force?: boolean } = {},
): Promise<DeviceState> {
  const blocked = blockedReason()
  if (blocked) return { problem: blocked, subscribed: false, repaired: false }
  if (!publicKey) {
    return { problem: 'The server has no notification keys configured.',
             subscribed: false, repaired: false }
  }
  if (Notification.permission !== 'granted') {
    // 'default' means it was never asked, or iOS forgot. Asking is allowed here
    // because Fix is a deliberate tap, not something happening in the background.
    if (!opts.force) {
      return { problem: 'Notifications are not switched on for this device.',
               subscribed: false, repaired: false }
    }
    const granted = await Notification.requestPermission()
    if (granted !== 'granted') {
      return { problem: 'iOS refused permission. Open Settings → Notifications → '
                        + `${appName()} and allow them, then tap Fix again.`,
               subscribed: false, repaired: false }
    }
  }

  try {
    const reg = await readyRegistration()
    let sub = await reg.pushManager.getSubscription()
    let repaired = false

    // Fix means "make it work", so throw away whatever is there and start clean —
    // an existing subscription can be intact yet no longer deliverable.
    let discarded = ''
    if (sub && opts.force) {
      discarded = sub.endpoint
      try { await sub.unsubscribe() } catch { /* keep going; subscribe may still work */ }
      sub = null
    }
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
      })
      repaired = true
    }
    // Always re-register: the endpoint may have changed without the app noticing,
    // and the server upserts by endpoint so this is cheap and idempotent.
    await api('/api/notifications/subscribe', { method: 'POST', body: sub.toJSON() })

    // Retire the endpoint we just replaced. Without this the server keeps counting a
    // subscription this phone no longer holds, so one device reads as two — and the
    // push service goes on accepting messages for it that can never be shown.
    // Done AFTER registering the replacement: unsubscribing the last device would
    // otherwise flip the whole feature off.
    if (discarded && discarded !== sub.endpoint) {
      try {
        await api('/api/notifications/unsubscribe',
                  { method: 'POST', body: { endpoint: discarded } })
      } catch { /* a stale row is untidy, not broken — it prunes itself on 410 */ }
    }
    return { problem: '', subscribed: true, repaired }
  } catch (e) {
    const raw = e instanceof Error ? e.message : String(e)
    // Chrome/Safari wording for "a subscription exists under a different VAPID key".
    const problem = /different applicationServerKey|already exists/i.test(raw)
      ? 'This device is registered under an older key. Turn the switch off, then on again.'
      : raw || 'Could not register this device.'
    return { problem, subscribed: false, repaired: false }
  }
}

export const sendTest = () =>
  api<{ preview: { title: string; body: string }; sent: number; devices: number }>(
    '/api/notifications/test', { method: 'POST' })
