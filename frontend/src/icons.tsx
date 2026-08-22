// Inline SVG icons (stroke-based, inherit currentColor). Keeps the bundle asset-free.
type P = { className?: string }
const S = (d: string) => (p: P) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" className={p.className}>
    {d.split('|').map((seg, i) => <path key={i} d={seg} />)}
  </svg>
)

export const IcHome = S('M3 10.5 12 3l9 7.5|M5 9.5V21h14V9.5')
export const IcModules = S('M4 4h6v6H4z|M14 4h6v6h-6z|M4 14h6v6H4z|M14 14h6v6h-6z')
export const IcBell = S('M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9|M13.7 21a2 2 0 0 1-3.4 0')
export const IcUser = S('M20 21a8 8 0 0 0-16 0|M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z')
export const IcLoans = S('M3 7h18v12H3z|M3 11h18|M7 15h4')
export const IcCards = S('M2 5h20v14H2z|M2 10h20|M6 15h4')
export const IcShield = S('M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z')
export const IcTrend = S('M3 17l6-6 4 4 8-8|M15 7h6v6')
export const IcWallet = S('M3 7h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z|M3 7l0-2h13v2|M17 13h.01')
export const IcCheck = S('M9 11l3 3L22 4|M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11')
export const IcImage = S('M3 3h18v18H3z|M3 15l5-5 4 4 3-3 6 6')
export const IcLock = S('M5 11h14v10H5z|M8 11V7a4 4 0 0 1 8 0v4')
export const IcDoc = S('M6 2h8l4 4v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z|M14 2v6h6|M8 13h8|M8 17h5')
// A lightbulb, the way Keep marks notes.
export const IcNote = S('M9 18h6|M10 22h4|M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z')
export const IcLogout = S('M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|M16 17l5-5-5-5|M21 12H9')
export const IcMoon = S('M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z')
export const IcTrash = S('M4 7h16|M10 11v6|M14 11v6|M6 7l1 13h10l1-13|M9 7V4h6v3')
export const IcEdit = S('M12 20h9|M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z')
// A flame — the streak, which is what a habit tracker is really about.
export const IcHabits = S('M12 22a7 7 0 0 0 7-7c0-3-2-5.2-3.3-7-1 1.1-2.2 1.6-3.2 1.6C13.4 6.3 12.3 3.2 10 2c.6 3-.9 4.7-2.4 6.2C6.1 9.8 5 11.8 5 15a7 7 0 0 0 7 7z')
