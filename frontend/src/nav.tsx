// Tiny navigation store — a route string + history stack, persisted so refresh
// restores the last screen. Mirrored into the browser History API so iOS Safari's
// back-swipe / the hardware-or-gesture back pops the stack (iPhones have no back
// button of their own). Tab-level routes reset the stack; everything else pushes.
// A one-shot `intent` lets one screen ask another to open (e.g. "add expense").
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

const KEY = 'finmate.route'
export const TAB_ROUTES = ['home', 'modules', 'expenses', 'reminders', 'gallery', 'profile']

interface Nav {
  route: string
  stack: string[]
  canBack: boolean
  go: (route: string, intent?: string) => void
  back: () => void
  takeIntent: () => string | null
  // Overlays (sheets, lightboxes) register a close handler so a back gesture
  // dismisses the top-most one before it touches the navigation stack.
  registerOverlay: (close: () => void) => number
  unregisterOverlay: (id: number) => void
  hasOverlay: () => boolean
}

const Ctx = createContext<Nav>(null!)

export function NavProvider({ children }: { children: ReactNode }) {
  const [stack, setStack] = useState<string[]>(() => {
    const saved = localStorage.getItem(KEY)
    return saved ? [saved] : ['home']
  })
  const stackRef = useRef(stack)
  stackRef.current = stack
  const intent = useRef<string | null>(null)
  const route = stack[stack.length - 1]

  const commit = (next: string[]) => {
    localStorage.setItem(KEY, next[next.length - 1])
    stackRef.current = next
    setStack(next)
  }

  const go = useCallback((r: string, wish?: string) => {
    intent.current = wish ?? null
    const prev = stackRef.current
    if (TAB_ROUTES.includes(r)) commit([r]) // switching tabs resets the stack
    else if (prev[prev.length - 1] !== r) commit([...prev, r])
  }, [])

  // User-initiated back drives the browser, which fires popstate → the pop happens there.
  const back = useCallback(() => { window.history.back() }, [])

  // LIFO stack of open overlays (sheets, lightboxes).
  const overlays = useRef<{ id: number; close: () => void }[]>([])
  const overlaySeq = useRef(0)
  const registerOverlay = useCallback((close: () => void) => {
    const id = ++overlaySeq.current
    overlays.current.push({ id, close })
    return id
  }, [])
  const unregisterOverlay = useCallback((id: number) => {
    overlays.current = overlays.current.filter((o) => o.id !== id)
  }, [])
  const hasOverlay = useCallback(() => overlays.current.length > 0, [])

  // A persistent history "buffer" entry catches every back gesture/button. On each
  // back we first dismiss the top overlay (if any), else pop the route stack, then
  // immediately re-add the buffer — so the app never falls off the bottom of its
  // history, matching how a native app behaves.
  useEffect(() => {
    window.history.pushState(null, '')
    const onPop = () => {
      if (overlays.current.length) {
        overlays.current[overlays.current.length - 1].close()
      } else {
        const prev = stackRef.current
        if (prev.length > 1) commit(prev.slice(0, -1))
      }
      window.history.pushState(null, '')
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // read-and-clear: a target screen consumes the pending intent exactly once
  const takeIntent = useCallback(() => {
    const v = intent.current
    intent.current = null
    return v
  }, [])

  return (
    <Ctx.Provider value={{ route, stack, canBack: stack.length > 1, go, back, takeIntent, registerOverlay, unregisterOverlay, hasOverlay }}>
      {children}
    </Ctx.Provider>
  )
}

export const useNav = () => useContext(Ctx)

// Overlays (Sheet, Lightbox) call this so a back gesture/button dismisses them
// first. Registers once on mount; always invokes the latest `onClose`.
export function useOverlayBack(onClose: () => void) {
  const { registerOverlay, unregisterOverlay } = useNav()
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const id = registerOverlay(() => closeRef.current())
    return () => unregisterOverlay(id)
  }, [registerOverlay, unregisterOverlay])
}
