// Theme: 'light' | 'dark' | 'system', persisted and applied via data-theme on <html>.
export type Theme = 'light' | 'dark' | 'system'
const KEY = 'finmate.theme'

export function getTheme(): Theme { return (localStorage.getItem(KEY) as Theme) || 'system' }

export function applyTheme(t: Theme) {
  localStorage.setItem(KEY, t)
  const root = document.documentElement
  if (t === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', t)
}
