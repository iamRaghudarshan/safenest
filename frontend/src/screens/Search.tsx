// One search box for everything: records, documents and photos in one list.
//
// Before this, finding "that electricity bill" meant remembering whether it was
// filed as an expense, a document or a photo, then searching that screen. The
// point of searching is not having to know where you put it.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useNav } from '../nav'
import { TopBar } from '../ui'
import type { SearchGroup, SearchHit, SearchResults } from '../types'

const KIND_ICON: Record<string, string> = {
  expenses: '💳', documents: '📄', reminders: '🔔', loans: '🏦',
  cards: '💳', insurance: '🛡️', investments: '📈', todos: '✓',
  vault: '🔒', photos: '🖼️',
}

const DEBOUNCE_MS = 250

export default function Search() {
  const { go } = useNav()
  const [q, setQ] = useState('')
  const [res, setRes] = useState<SearchResults | null>(null)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLInputElement>(null)
  // Tracks the newest request so a slow early reply can't overwrite a fast later
  // one — the classic way a search box ends up showing results for a prefix of
  // what you typed.
  const latest = useRef(0)

  useEffect(() => { box.current?.focus() }, [])

  const run = useCallback(async (text: string) => {
    const mine = ++latest.current
    if (text.trim().length < 2) { setRes(null); setBusy(false); return }
    setBusy(true)
    try {
      const out = await api<SearchResults>(`/api/search?q=${encodeURIComponent(text)}`)
      if (mine === latest.current) setRes(out)
    } catch {
      if (mine === latest.current) setRes(null)
    } finally {
      if (mine === latest.current) setBusy(false)
    }
  }, [])

  useEffect(() => {
    const t = window.setTimeout(() => run(q), DEBOUNCE_MS)
    return () => window.clearTimeout(t)
  }, [q, run])

  const understood = res?.understood ?? {}
  const chips = [
    ...(understood.modules ?? []),
    understood.year ? String(understood.year) : '',
  ].filter(Boolean)

  return (
    <div className="screen">
      <TopBar title="Search" />

      <div className="searchbar">
        <span className="searchbar-ic">🔎</span>
        <input ref={box} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search everything — bills, photos, policies…"
          autoComplete="off" autoCorrect="off" spellCheck={false} />
        {q && <button className="searchbar-x" onClick={() => setQ('')} aria-label="Clear">×</button>}
      </div>

      {/* Showing what was understood makes an empty result explainable: you can
          see it filtered to one module or one year, rather than guessing. */}
      {chips.length > 0 && (
        <div className="sr-understood">
          {chips.map(c => <span key={c} className="sr-chip">{c}</span>)}
        </div>
      )}

      {busy && !res && <p className="muted sr-note">Searching…</p>}

      {res && res.total === 0 && (
        <p className="muted sr-note">
          Nothing found for “{res.query}”.
          {chips.length > 0 && ' Try removing a word to widen the search.'}
        </p>
      )}

      {res?.groups.map(g => <Group key={g.kind} group={g} onOpen={go} />)}

      {!res && q.trim().length < 2 && (
        <div className="sr-hints">
          <p className="muted">Try:</p>
          <ul>
            <li>a shop or company name — it looks inside scanned bills too</li>
            <li>“show my cards”, “my documents”</li>
            <li>“electricity bills last year”</li>
          </ul>
        </div>
      )}
    </div>
  )
}

function Group({ group, onOpen }: { group: SearchGroup; onOpen: (r: string) => void }) {
  return (
    <section className="sr-group">
      <h2 className="set-head">
        {KIND_ICON[group.kind] ?? '•'} {group.label}
        <span className="sr-count">{group.count}</span>
      </h2>
      <div className="set-card">
        {group.items.map(item => (
          <Hit key={`${group.kind}-${item.id}`} item={item} onOpen={onOpen} />
        ))}
      </div>
    </section>
  )
}

function Hit({ item, onOpen }: { item: SearchHit; onOpen: (r: string) => void }) {
  return (
    <button className="set-row" onClick={() => onOpen(item.route)}>
      {item.thumb_url
        ? <img className="sr-thumb" src={item.thumb_url} alt="" loading="lazy" />
        : <span className="set-ic" style={{ background: 'var(--ink-faint)' }}>
          {KIND_ICON[item.route] ?? '•'}
        </span>}
      <span className="set-text">
        <span className="set-label">{item.title}</span>
        <span className="set-sub">
          {item.sub}
          {/* Says why it matched when the words were only inside a scan, or when
              the match is a resemblance rather than the words being present. */}
          {item.inside && <span className="sr-why"> found inside the scan</span>}
          {item.matched === 'looks' && <span className="sr-why"> looks similar</span>}
        </span>
      </span>
      {item.amount != null && item.amount > 0 && (
        <span className="set-value">₹{item.amount.toLocaleString('en-IN')}</span>
      )}
      {item.when && item.amount == null && <span className="set-value">{item.when}</span>}
    </button>
  )
}
