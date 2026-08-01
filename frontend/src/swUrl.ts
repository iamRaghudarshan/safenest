// The one place the service-worker URL is written.
//
// It was previously inline in main.tsx, and the notification repair registered a
// bare '/sw.js' — a DIFFERENT script URL, which the browser treats as a second
// worker. Two registrations fighting over one scope is exactly the kind of fault
// that ends in "push says delivered but nothing appears".
//
// The `?v=` query gives the script a distinct cache key at every layer (browser,
// CDN) so a proxy that ignored the no-cache header can't pin anyone to an old
// worker. Bump it whenever public/sw.js changes.
//
// It is doing real work here, not guarding against a hypothetical. Measured on
// 31 July 2026: the origin sends `no-cache, must-revalidate` for /sw.js and
// Cloudflare rewrites it to `max-age=14400` — four hours. Because the worker
// decides which assets the app loads, a device stays on the previous build for
// that whole window, which looked exactly like a new feature "not working".
// Bumping this is the immediate escape; the lasting fix is Cloudflare →
// Caching → Configuration → Browser Cache TTL → "Respect Existing Headers".
export const SW_URL = '/sw.js?v=9'
export const SW_OPTIONS: RegistrationOptions = { updateViaCache: 'none' }
