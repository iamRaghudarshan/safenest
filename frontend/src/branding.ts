// The app's own name and icon.
//
// Every place that used to say "FinMate" in the interface now reads from here, so
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
export const FALLBACK: Branding = {
  app_name: 'SafeNest',
  short_name: 'SafeNest',
  tagline: '',
  theme_color: '#5b3df5',
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

/** Load the branding once at startup. Failure is not fatal — the fallback stands. */
export async function loadBranding() {
  try {
    brand.set(await api<Branding>('/api/branding', { auth: false }))
  } catch {
    apply(FALLBACK)
  }
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
