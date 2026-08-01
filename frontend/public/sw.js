// FinMate service worker — offline app-shell + last-seen data.
//
// Strategy (order matters):
//  • sensitive /api          → never touched, straight to network
//  • /api GET                → network-first, cached copy is the offline fallback
//  • navigations + index.html→ NETWORK-FIRST, cached shell only when offline
//  • hashed /assets/*        → cache-first (filenames are content-hashed, so a new
//                              build is a new URL and can never be served stale)
//  • other static files      → stale-while-revalidate
//
// The navigation rule is the important one: serving index.html cache-first pins the
// app to whatever JS bundle it referenced when first cached, so users never receive
// a new deploy. Financial data still needs the backend to refresh or save; offline
// only re-shows what was already fetched.
const CACHE = 'finmate-v8'
const SHELL = '/'

// Never persist these to disk — a cached copy outlives the session and is readable
// without a token. Offline access isn't worth it for secrets or private files.
const NEVER_CACHE = ['/api/vault', '/api/documents', '/api/auth', '/api/gallery/media']
const isSensitive = (path) => NEVER_CACHE.some((p) => path.startsWith(p))

self.addEventListener('install', () => self.skipWaiting())

self.addEventListener('activate', (e) => e.waitUntil((async () => {
  const keys = await caches.keys()
  await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  await self.clients.claim()
})()))

// Lets the page ask a waiting worker to take over immediately.
self.addEventListener('message', (e) => { if (e.data === 'skip-waiting') self.skipWaiting() })

/* ---------------------------------------------------------------- push ---- */

// The daily digest and one-off alerts (an export finishing) both arrive here.
// `renotify` with a tag means a second notification of the SAME kind replaces the
// first rather than stacking up — but the tag comes from the payload, so an export
// alert does not silently overwrite the day's digest.
self.addEventListener('push', (event) => {
  let data = {}
  try { data = event.data ? event.data.json() : {} } catch { data = {} }
  const title = data.title || 'FinMate'
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || 'You have items due.',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || 'finmate-digest',
    renotify: true,
    data: { url: data.url || '/' },
  }))
})

// Tapping the notification focuses an open tab if there is one, rather than
// opening a duplicate copy of the app.
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    for (const c of all) {
      if (c.url.includes(self.location.origin)) {
        await c.focus()
        if ('navigate' in c && target !== '/') await c.navigate(target).catch(() => {})
        return
      }
    }
    await self.clients.openWindow(target)
  })())
})

async function put(req, res) {
  if (res && res.ok && res.type === 'basic') {
    const cache = await caches.open(CACHE)
    cache.put(req, res.clone())
  }
  return res
}

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  if (isSensitive(url.pathname)) return

  // API — freshest data wins, cache is the offline safety net.
  if (url.pathname.startsWith('/api/')) {
    event.respondWith((async () => {
      try {
        return await put(req, await fetch(req))
      } catch {
        const hit = await caches.match(req)
        return hit || new Response(JSON.stringify({ offline: true }), {
          status: 503, headers: { 'Content-Type': 'application/json' },
        })
      }
    })())
    return
  }

  // Navigations / the HTML shell — always try the network so a new build is picked
  // up on the very next load; fall back to the cached shell only when truly offline.
  if (req.mode === 'navigate' || url.pathname === '/' || url.pathname.endsWith('.html')) {
    event.respondWith((async () => {
      try {
        const res = await fetch(req)
        await put(new Request(SHELL), res)
        return res
      } catch {
        return (await caches.match(SHELL)) || (await caches.match(req)) || Response.error()
      }
    })())
    return
  }

  // Content-hashed build output is immutable — safe and fast to serve from cache.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith((async () => {
      const hit = await caches.match(req)
      if (hit) return hit
      try { return await put(req, await fetch(req)) } catch { return Response.error() }
    })())
    return
  }

  // Everything else (icons, manifest): serve fast, refresh in the background.
  event.respondWith((async () => {
    const hit = await caches.match(req)
    const net = fetch(req).then((res) => put(req, res)).catch(() => null)
    return hit || (await net) || Response.error()
  })())
})
