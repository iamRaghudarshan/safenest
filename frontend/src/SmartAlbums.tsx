// Albums the library already implies, offered rather than created.
//
// The grouping comes from photo vectors that were already stored for search, so
// this costs no new model and no download. Suggestions only: a gallery that
// silently grows albums nobody asked for is tedious to undo, and the whole value
// is in the one tap that says "yes, that's a real group".
import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from './api'
import { useToast } from './toast'
import type { AlbumSuggestion } from './types'

export function SmartAlbums({ onCreated }: { onCreated?: () => void }) {
  const toast = useToast()
  const [list, setList] = useState<AlbumSuggestion[] | null>(null)
  const [busy, setBusy] = useState('')
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      const r = await api<{ suggestions: AlbumSuggestion[] }>('/api/gallery/albums/suggested')
      setList(r.suggestions)
    } catch { setList([]) }
  }, [])

  useEffect(() => { load() }, [load])

  const shown = (list ?? []).filter(s => !s.exists && !hidden.has(s.name))
  if (!shown.length) return null

  async function accept(s: AlbumSuggestion) {
    setBusy(s.name)
    try {
      await api('/api/gallery/albums/suggested', {
        method: 'POST', body: { name: s.name, photo_ids: s.photo_ids },
      })
      toast(`“${s.name}” created`)
      setHidden(h => new Set(h).add(s.name))
      onCreated?.()
    } catch (e) { toast(errorMessage(e, 'Could not create that album')) }
    finally { setBusy('') }
  }

  return (
    <section className="smart">
      <h2 className="set-head">Suggested albums</h2>
      <div className="smart-scroll">
        {shown.map(s => (
          <div key={s.name} className="smart-card">
            {s.cover_url
              ? <img src={s.cover_url} alt="" loading="lazy" />
              : <div className="smart-blank">🖼️</div>}
            <div className="smart-body">
              <div className="smart-name">{s.name}</div>
              <div className="smart-sub">{s.count} photos</div>
            </div>
            <div className="smart-actions">
              <button className="btn primary sm" disabled={!!busy} onClick={() => accept(s)}>
                {busy === s.name ? '…' : 'Create'}
              </button>
              {/* Dismiss is local only: the grouping is recomputed from the
                  photos each time, so a suggestion turned down today will
                  reappear if the library changes enough to still suggest it. */}
              <button className="btn ghost sm" disabled={!!busy}
                onClick={() => setHidden(h => new Set(h).add(s.name))}>
                Not now
              </button>
            </div>
          </div>
        ))}
      </div>
      <p className="set-foot">
        Worked out from the photos themselves. Nothing is created until you say so.
      </p>
    </section>
  )
}
