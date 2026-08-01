import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Stamped into the bundle so Profile → App & storage can show which build is
// actually running — the quickest way to tell a stale cache from a real bug.
const BUILD_ID = new Date().toISOString().slice(0, 16).replace('T', ' ')

// Dev server proxies /api → FastAPI on 8000 so the SPA and API share an origin.
export default defineConfig({
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  plugins: [react()],
  build: {
    // Keep the -webkit- prefixes older iOS still needs. The default target assumes
    // a modern baseline and strips them, which breaks the appearance reset that
    // stops <input type="date"> overflowing its column in Safari.
    cssTarget: 'safari14',
  },
  server: {
    port: 5173,
    host: true,          // bind to 0.0.0.0 so phones/tablets on the same Wi-Fi can reach it
    proxy: {
      // The proxy runs on the PC, so the backend can stay on localhost.
      // Photos are served from /api/gallery/media — there is no public /uploads mount.
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true },
    },
  },
})
