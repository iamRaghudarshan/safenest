// Formatting helpers — money in INR, dates as dd-mm-yyyy with 12-hour AM/PM times.
//
// FinMate runs on India Standard Time everywhere. The server writes every stored
// timestamp as IST wall-clock (see backend/app/ist.py), so values arriving from the
// API are already in the right zone and are reformatted here, not converted.
//
// The one place real timezone maths is needed is "what is today?", because that
// comes from the device clock — and the device may be a laptop carried abroad, or
// a phone left on the wrong timezone. Those go through istNow() below.

const IST_OFFSET_MIN = 330 // UTC+05:30

/** A Date whose LOCAL fields read as IST wall-clock, whatever zone the device is in. */
export function istNow(from: Date = new Date()): Date {
  return new Date(from.getTime() + (IST_OFFSET_MIN + from.getTimezoneOffset()) * 60000)
}

const pad = (n: number) => String(n).padStart(2, '0')
const isoOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`


export function money(n: number | string | null | undefined, compact = false): string {
  const v = Number(n || 0)
  if (compact) {
    if (Math.abs(v) >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`
    if (Math.abs(v) >= 1e5) return `₹${(v / 1e5).toFixed(2)}L`
    if (Math.abs(v) >= 1e3) return `₹${(v / 1e3).toFixed(1)}K`
  }
  return '₹' + v.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

// ISO (yyyy-mm-dd) -> dd-mm-yyyy for display
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.slice(0, 10).split('-')
  if (!y || !m || !d) return iso
  return `${d}-${m}-${y}`
}

/** "4:05 PM" from an ISO datetime, or '' when it carries no time. */
export function fmtTime(iso: string | null | undefined): string {
  const hhmm = (iso || '').slice(11, 16)
  if (!/^\d{2}:\d{2}$/.test(hhmm)) return ''
  const [h, m] = hhmm.split(':').map(Number)
  return `${h % 12 || 12}:${pad(m)} ${h >= 12 ? 'PM' : 'AM'}`
}

// ISO datetime -> "dd-mm-yyyy, 4:05 PM". Falls back to the date alone when the
// value carries no time part.
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const day = fmtDate(iso)
  const time = fmtTime(iso)
  return time ? `${day}, ${time}` : day
}

// relative "due" label from a day-delta
export function dueLabel(days: number | null | undefined): { text: string; tone: 'danger' | 'warn' | 'ok' | 'muted' } {
  if (days == null) return { text: 'No date', tone: 'muted' }
  if (days < 0) return { text: `${Math.abs(days)}d overdue`, tone: 'danger' }
  if (days === 0) return { text: 'Due today', tone: 'danger' }
  if (days === 1) return { text: 'Due tomorrow', tone: 'warn' }
  if (days <= 7) return { text: `In ${days} days`, tone: 'warn' }
  return { text: `In ${days} days`, tone: 'muted' }
}

/** Today in India, yyyy-mm-dd.
 *
 *  Was `new Date().toISOString()`, which is UTC — so between midnight and 5:30 AM
 *  IST it returned YESTERDAY, and a late-night expense was filed against the wrong
 *  day. Anchored to IST it is right at every hour, on any device. */
export const todayISO = () => isoOf(istNow())

/** Any IST calendar day offset from today — `istDayISO(-1)` is yesterday. */
export const istDayISO = (offsetDays = 0) =>
  isoOf(istNow(new Date(Date.now() + offsetDays * 86400000)))
