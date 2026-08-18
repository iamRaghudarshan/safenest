import { StrictMode } from 'react'
import { SW_URL, SW_OPTIONS } from './swUrl'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { NatureBackdrop } from './NatureBackdrop'
import { AuthProvider } from './auth'
import { NavProvider } from './nav'
import { ToastProvider } from './toast'
import { AttentionProvider } from './attention'
import { UploadProvider } from './upload'
import { applyTheme, getTheme } from './theme'
import { initLetterboxCompensation } from './letterbox'
import { loadBranding } from './branding'

applyTheme(getTheme())
// The app's name and icon come from the server, so a rename by an admin reaches
// the tab title and favicon too. Fire-and-forget: a failure leaves the compiled
// defaults in place rather than blocking the first paint.
loadBranding()
// Before React renders, so the shell is the right size on first paint.
initLetterboxCompensation()

// Register the offline service worker only in a secure context (HTTPS or
// localhost). On the plain-http dev server it stays off, so HMR isn't cached.
if ('serviceWorker' in navigator &&
    (location.protocol === 'https:' || location.hostname === 'localhost')) {
  window.addEventListener('load', async () => {
    try {
      // URL and options live in swUrl.ts so the notification repair registers the
      // very same worker rather than a competing one.
      const reg = await navigator.serviceWorker.register(SW_URL, SW_OPTIONS)

      // When a new worker takes over, the page needs a reload to pick up the matching
      // JS/CSS — but reloading the instant that happens destroys whatever the user was
      // doing (an open sheet, a half-typed form) and reads as "the button did nothing".
      // So: reload immediately only during the initial hand-off right after load, and
      // otherwise wait until the app is next brought to the foreground.
      const loadedAt = Date.now()
      let reloading = false
      let pending = false

      const applyUpdate = () => {
        if (reloading) return
        reloading = true
        location.reload()
      }

      navigator.serviceWorker.addEventListener('controllerchange', () => {
        // Within a few seconds of load this is the first-install claim, and nothing
        // can be in progress yet — safe to swap straight away.
        if (Date.now() - loadedAt < 5000) applyUpdate()
        else pending = true
      })
      reg.addEventListener('updatefound', () => {
        const sw = reg.installing
        sw?.addEventListener('statechange', () => {
          if (sw.state === 'installed' && navigator.serviceWorker.controller) {
            sw.postMessage('skip-waiting')
          }
        })
      })

      // Check for a new build on launch and whenever the app is re-opened. A build
      // that arrived mid-session is applied here, on return to the app, rather than
      // yanking the page out from under an active tap.
      reg.update().catch(() => {})
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return
        if (pending) { applyUpdate(); return }
        reg.update().catch(() => {})
      })
    } catch { /* offline support is optional */ }
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <NatureBackdrop />
    <AuthProvider>
      <NavProvider>
        <ToastProvider>
          <AttentionProvider>
            <UploadProvider>
              <App />
            </UploadProvider>
          </AttentionProvider>
        </ToastProvider>
      </NavProvider>
    </AuthProvider>
  </StrictMode>,
)
