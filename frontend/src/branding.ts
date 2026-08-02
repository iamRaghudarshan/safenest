// The app's own name and icon.
//
// Every place that used to say "the app" in the interface now reads from here, so
// renaming the app is one admin action rather than a rebuild. The values arrive
// from /api/branding, which is deliberately unauthenticated — the login screen
// has to know what the app is called before anyone has signed in.
//
// Applying the name to the DOM (title, favicon, home-screen icon) happens here
// too, in one place. Scattering that across components is how a rename ends up
// half-applied: the header changes, the browser tab does not.
import { useEffect, useState } from 'react'
import { api } from './api'

export interface Branding {
  app_name: string
  short_name: string
  tagline: string
  theme_color: string
  icon_version: number
  icons: Record<string, string>
}

// What the app is called before the server has answered. Matching index.html
// keeps the tab from flickering through a placeholder on every load.
/** What the page already knows about itself, before any request is made.
 *
 *  main.py::_branded_index() rewrites <title> and theme-color with the real name
 *  on the way out, precisely so a cold load never shows the wrong one. Reading
 *  them back is free and always right, where a hard-coded name is a second place
 *  the branding lives and a second place it can be wrong: this said "SafeNest"
 *  regardless, so a copy renamed to anything else showed the old name until the
 *  first request answered — and if that request failed, for ever.
 */
function fromPage<T>(pick: () => T | null | undefined, spare: T): T {
  try {
    const v = pick()
    return (v === null || v === undefined || v === '' ? spare : v)
  } catch {
    return spare
  }
}

const PAGE_NAME = fromPage(() => document.title.trim(), 'App')

export const FALLBACK: Branding = {
  app_name: PAGE_NAME,
  short_name: PAGE_NAME,
  tagline: '',
  theme_color: fromPage(
    () => document.head.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.content,
    '#5b3df5'),
  icon_version: 0,
  icons: { '32': '/icon-192.png', '180': '/apple-touch-icon.png', '192': '/icon-192.png', '512': '/icon-512.png' },
}

let current: Branding = FALLBACK
const listeners = new Set<(b: Branding) => void>()

export const brand = {
  get: () => current,
  // The unsubscribe must return nothing: React treats a returned value as a
  // cleanup function, and Set.delete's boolean is not one.
  subscribe(fn: (b: Branding) => void): () => void {
    listeners.add(fn)
    return () => { listeners.delete(fn) }
  },
  set(b: Branding) {
    current = b
    apply(b)
    listeners.forEach((fn) => fn(b))
  },
}

/** Point an existing <link rel="..."> at a new URL, creating it if absent. */
function link(rel: string, href: string, type?: string) {
  let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)
  if (!el) {
    el = document.createElement('link')
    el.rel = rel
    document.head.appendChild(el)
  }
  if (type) el.type = type
  el.href = href
}

/** Push the name and icon into the page itself. */
function apply(b: Branding) {
  document.title = b.app_name

  link('icon', b.icons['32'] || b.icons['192'], 'image/png')
  link('apple-touch-icon', b.icons['180'])

  const theme = document.head.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
  if (theme) theme.content = b.theme_color

  // iOS reads this for the home-screen label. It is only consulted at
  // Add-to-Home-Screen time, so an icon already on a phone keeps its old name
  // until it is removed and re-added — worth knowing before someone reports it
  // as a bug.
  const title = document.head.querySelector<HTMLMetaElement>('meta[name="apple-mobile-web-app-title"]')
  if (title) title.content = b.short_name

  // Re-request the manifest so an install prompt picks up the new name. Without
  // the cache-busting query the browser keeps serving the one it already parsed.
  const man = document.head.querySelector<HTMLLinkElement>('link[rel="manifest"]')
  if (man) man.href = `/manifest.webmanifest?v=${b.icon_version}-${encodeURIComponent(b.short_name)}`
}

/** Load the branding, and keep trying until it answers.
 *
 *  ONE ATTEMPT WAS NOT ENOUGH, AND THE FAILURE LOOKED LIKE A DIFFERENT BUG.
 *  A packaged copy opens the browser two seconds after launch, which on a cold
 *  start is before the server is listening. That single request failed, the
 *  fallback stood for the rest of the page's life, and the first screen anybody
 *  saw was the stock rupee mark and the stock tagline under the right name —
 *  reported, reasonably, as "the logo is the old one".
 *
 *  It reads as a branding fault and it is a timing one, which is why it survived
 *  several attempts to fix the branding. The fallback exists to stop the tab
 *  flickering through a placeholder, not to be what someone ends up looking at.
 */
export async function loadBranding() {
  // Roughly ten seconds in total, front-loaded: a server that is coming up
  // usually answers within a second or two, and anything slower is worth waiting
  // out rather than settling for the placeholder.
  const waits = [0, 300, 700, 1200, 2000, 2000, 3000]
  for (let i = 0; i < waits.length; i++) {
    if (waits[i]) await new Promise((r) => setTimeout(r, waits[i]))
    try {
      brand.set(await api<Branding>('/api/branding', { auth: false }))
      return
    } catch {
      // Keep the placeholder on screen and try again.
    }
  }
  apply(FALLBACK)
}

/** The app's current name, read without subscribing.
 *
 *  For prose inside components that have no other reason to re-render, and for
 *  plain modules that cannot use a hook at all. The value is loaded once before
 *  the first screen paints, and a rename closes the sheet that changed it — which
 *  re-renders the tree anyway — so a non-reactive read is accurate in practice
 *  and far less invasive than threading a hook through every component that
 *  happens to mention the app by name.
 */
export const appName = () => current.app_name

/** Subscribe a component to the current name and icon. */
export function useBranding(): Branding {
  const [b, setB] = useState<Branding>(current)
  useEffect(() => brand.subscribe(setB), [])
  return b
}
