// Minimal toast: a context + auto-dismissing bubble.
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'

const Ctx = createContext<(msg: string) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null)
  const show = useCallback((m: string) => {
    setMsg(m)
    window.setTimeout(() => setMsg(null), 2400)
  }, [])
  return (
    <Ctx.Provider value={show}>
      {children}
      {msg && <div className="toast">{msg}</div>}
    </Ctx.Provider>
  )
}

export const useToast = () => useContext(Ctx)
