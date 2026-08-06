import { useCallback, useEffect, useRef, useState } from 'react'
import { SmartAlbums } from '../SmartAlbums'
import { api, errorMessage } from '../api'
import { useNav, useOverlayBack } from '../nav'
import { useAuth } from '../auth'
import { useToast } from '../toast'
import { useUpload } from '../upload'
import { TopBar, Spinner, Empty, Sheet, Field } from '../ui'
import { PullToRefresh } from '../PullToRefresh'
import { Zoomable } from '../Zoomable'
import { PhotoIndexCard } from '../PhotoIndex'
import { IcTrash } from '../icons'
import { fmtDate, fmtDateTime } from '../format'
import { formatBytes } from '../maintenance'
import type {
  Photo, PhotoInfo, PersonSummary, MemoryGroup, DuplicatesData, DuplicateGroup, AlbumSummary,
  IndexStatus,
} from '../types'
import { appName } from '../branding'

type Tab = 'all' | 'fav' | 'albums' | 'people' | 'memories'

export default function Gallery() {
  const { back, canBack, takeIntent } = useNav()
  const { can } = useAuth()
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  // One picker, two intentions. There used to be a second hidden input purely so
  // the two could say different things afterwards; remembering which button was
  // pressed does the same job without a duplicate control to keep in step.
  const backupIntent = useRef(false)
  const u = useUpload()
  const PAGE = 150
  const [photos, setPhotos] = useState<Photo[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [more, setMore] = useState(false)
  const [tab, setTab] = useState<Tab>('all')
  // Bumped when a suggested album is accepted. AlbumsGrid loads its own list, so
  // changing its key is the honest way to make it refetch without threading a
  // callback down through it.
  const [albumsRev, setAlbumsRev] = useState(0)
  const [view, setView] = useState<Photo | null>(null)
  const [person, setPerson] = useState<PersonSummary | null>(null) // drill-into a person
  const [album, setAlbum] = useState<AlbumSummary | null>(null)    // drill-into an album
  const [trashOpen, setTrashOpen] = useState(false)
  const [dupOpen, setDupOpen] = useState(false)
  const [backupOpen, setBackupOpen] = useState(false)
  // What the queue is doing while it is being built. The UploadBar owns progress
  // once there is a queue to report on, but walking a 500-photo selection takes
  // seconds before the first item exists, and that silence is what people read
  // as the button having failed.
  const [queueStatus, setQueueStatus] = useState<string | null>(null)
  const [dupMode, setDupMode] = useState<'exact' | 'similar'>('exact')
  const canEdit = can('gallery')

  // Typing filters the grid, but each keystroke must not become a request. The
  // committed term lags the field by a beat and is what the loader actually uses.
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState('')
  // Two ways to search: by what a photo is CALLED, or by what is IN it.
  const [smart, setSmart] = useState(false)
  const [index, setIndex] = useState<IndexStatus | null>(null)
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(typed.trim()), 300)
    return () => window.clearTimeout(t)
  }, [typed])

  // deep-link from another screen (e.g. Profile) to open the finder in a given mode
  useEffect(() => {
    const it = takeIntent()
    if (it === 'duplicates') { setDupMode('exact'); setDupOpen(true) }
    else if (it === 'similar') { setDupMode('similar'); setDupOpen(true) }
  }, [takeIntent])
  const isFav = tab === 'fav'
  const offsetRef = useRef(0)   // next row to fetch
  const doneRef = useRef(false) // no more pages
  const busyRef = useRef(false) // a fetch is in flight

  // Paginated fetch. reset=true starts a fresh page 0 (used on mount, tab switch,
  // pull-to-refresh); reset=false appends the next page for infinite scroll.
  const load = useCallback(async (reset: boolean, silent = false) => {
    if (busyRef.current) return
    if (!reset && doneRef.current) return
    busyRef.current = true
    const off = reset ? 0 : offsetRef.current
    if (reset && !silent) setLoading(true)
    if (!reset) setMore(true)
    try {
      const p = new URLSearchParams({ offset: String(off), limit: String(PAGE) })
      if (isFav) p.set('fav', '1')
      if (query) p.set('q', query)
      if (query && smart) p.set('smart', '1')
      const d = await api<{ items: Photo[]; total: number }>(`/api/gallery?${p}`)
      setTotal(d.total)
      setPhotos((prev) => (reset ? d.items : [...prev, ...d.items]))
      offsetRef.current = off + d.items.length
      doneRef.current = d.items.length < PAGE
    } catch { /* ignore */ }
    finally { setLoading(false); setMore(false); busyRef.current = false }
  }, [isFav, query, smart])

  // (re)load page 0 whenever the tab (all ↔ fav) or the search term changes
  useEffect(() => { offsetRef.current = 0; doneRef.current = false; load(true) }, [load])

  // silent reload for pull-to-refresh (keeps the grid on screen)
  const refresh = useCallback(async () => {
    offsetRef.current = 0; doneRef.current = false; await load(true, true)
  }, [load])

  // refresh the grid as each upload batch completes
  useEffect(() => u.onBatchDone(() => refresh()), []) // eslint-disable-line react-hooks/exhaustive-deps

  async function pick(files: FileList | File[] | null, backup = false) {
    const arr = files ? Array.from(files) : []
    if (!arr.length) return   // the picker was dismissed; nothing happened, say nothing
    const n = arr.length
    const plural = n === 1 ? '' : 's'
    setQueueStatus(`Reading ${n.toLocaleString()} photo${plural}…`)
    try {
      // Handed over in chunks so the upload manager starts sending — and the
      // progress bar appears — while the rest of the selection is still being
      // walked. `persist` is decided here from the WHOLE selection, because each
      // chunk on its own looks small enough to be worth persisting and the point
      // of the budget is that a big backup persists nothing.
      const CHUNK = 30
      const persist = n <= CHUNK
      let queued = 0
      for (let i = 0; i < arr.length; i += CHUNK) {
        queued += await u.enqueue(arr.slice(i, i + CHUNK), { persist })
        setQueueStatus(`${queued.toLocaleString()} of ${n.toLocaleString()} photo${plural} queued…`)
      }
      const skipped = n - queued
      if (queued === 0) {
        toast(`Already here — all ${n.toLocaleString()} photo${plural} had been uploaded before`)
        return
      }
      // One sentence, because four toasts in a row for one tap is not four times
      // the reassurance.
      const also = skipped > 0 ? `; ${skipped.toLocaleString()} already here` : ''
      toast(backup
        ? `Backing up ${queued.toLocaleString()} photo${queued === 1 ? '' : 's'}${also} — carry on, it runs in the background`
        : `Queued ${queued.toLocaleString()} photo${queued === 1 ? '' : 's'} for upload${also}`)
    } finally {
      setQueueStatus(null)
    }
  }

  async function toggleFav(p: Photo) {
    await api(`/api/gallery/${p.id}/favourite`, { method: 'POST' })
    setPhotos((ps) => ps.map((x) => x.id === p.id ? { ...x, is_favourite: x.is_favourite ? 0 : 1 } : x))
    setView((v) => v && v.id === p.id ? { ...v, is_favourite: v.is_favourite ? 0 : 1 } : v)
  }

  async function trash(p: Photo) {
    await api(`/api/gallery/${p.id}`, { method: 'DELETE' })
    setPhotos((ps) => ps.filter((x) => x.id !== p.id)); setTotal((t) => Math.max(0, t - 1))
    offsetRef.current = Math.max(0, offsetRef.current - 1)
    setView(null); toast('Moved to trash')
  }

  // person drill-down is its own screen
  if (person) return <PersonView person={person} onBack={() => setPerson(null)} onOpen={setView} view={view} setView={setView}
    toggleFav={toggleFav} trash={trash} canEdit={canEdit} />

  // album drill-down is its own screen
  if (album) return <AlbumView album={album} onBack={() => setAlbum(null)} canEdit={canEdit} />

  // trash is its own screen; restoring reloads the main grid
  if (trashOpen) return <TrashView onBack={() => { setTrashOpen(false); refresh() }} canEdit={canEdit} />

  // duplicate finder is its own screen; resolving reloads the main grid
  if (dupOpen) return <DuplicatesView initialMode={dupMode} onBack={(changed) => { setDupOpen(false); if (changed) refresh() }} canEdit={canEdit} />

  const shown = photos // server already filters favourites when the ★ tab is active
  const headerRight = (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      {canEdit && (
        <button className="icon-btn" onClick={() => setDupOpen(true)} aria-label="Find duplicates" title="Find duplicates">
          <IcDuplicate className="ic" />
        </button>
      )}
      <button className="icon-btn" onClick={() => setTrashOpen(true)} aria-label="Trash"><IcTrash className="ic" /></button>
    </div>
  )

  function openPicker(backup: boolean) {
    backupIntent.current = backup
    fileRef.current?.click()
  }

  const countLabel = query
    ? `${total.toLocaleString()} match${total === 1 ? '' : 'es'}`
    : isFav
      ? `${total} favourite${total === 1 ? '' : 's'}`
      : `${total.toLocaleString()} photo${total === 1 ? '' : 's'}`
  const searchable = tab === 'all' || tab === 'fav'

  return (
    <div className="screen">
      <TopBar title="Gallery" sub={countLabel} onBack={canBack ? back : undefined} right={headerRight} />
      {/* Two separate actions, because they are two different intentions and
          collapsing them lost one of them. Adding a few photos you have just
          taken is not the same job as copying a phone's whole library across,
          and the second needs saying out loud before it starts.

          They sit here rather than in the TopBar because on a phone the header
          had to shrink them to a size that was easy to miss and hard to hit. */}
      {canEdit && (
        <div className="gallery-actions">
          <button className="btn sm" onClick={() => openPicker(false)}>
            {u.uploading ? 'Uploading…' : '＋ Add photos'}
          </button>
          <button className="btn sm ghost" onClick={() => setBackupOpen(true)}
            aria-label="Back up my photos">☁ Back up</button>
        </div>
      )}
      {queueStatus && <div className="upload-inline" aria-live="polite">{queueStatus}</div>}
      {/* accept is the wildcard alone. Listing .heic/.heif beside it makes iOS
          treat the control as an extension filter rather than "photos", and its
          picker then hands back a fraction of a large selection. HEIC files are
          image/heic, so the wildcard already covers them.

          Positioned off-screen rather than `hidden`: iOS Safari will not open the
          picker for an input in a subtree it considers non-rendered, so the
          button silently did nothing on exactly the device this matters on. */}
      <input ref={fileRef} type="file" accept="image/*" multiple className="file-offscreen"
        onChange={(e) => {
          // Snapshot before resetting the input: clearing `value` empties its
          // FileList, so reading it afterwards hands `pick` nothing at all.
          const files = Array.from(e.target.files ?? [])
          const backup = backupIntent.current
          e.currentTarget.value = ''   // so picking the same photo again re-fires change
          pick(files, backup).catch(() => toast('Those photos could not be queued'))
        }} />

      <div className="seg4 five">
        {(['all', 'fav', 'albums', 'people', 'memories'] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
            {t === 'all' ? 'All' : t === 'fav' ? '★' : t === 'albums' ? 'Albums'
              : t === 'people' ? 'People' : 'Memories'}
          </button>
        ))}
      </div>

      {searchable && (
        <>
          <div className="searchbar">
            <span className="searchbar-ic" aria-hidden="true">{smart ? '✨' : '🔍'}</span>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} type="search"
              enterKeyHint="search" autoComplete="off" autoCorrect="off" autoCapitalize="none"
              placeholder={smart ? 'Describe the photo — beach, cake, my dog'
                                 : 'Search by name, person, album, camera or year'}
              aria-label="Search photos" />
            {!!typed && (
              <button className="searchbar-x" onClick={() => setTyped('')} aria-label="Clear search">✕</button>
            )}
          </div>

          {/* Only offered once the models are installed — a mode that silently
              returns nothing is worse than not showing it. */}
          {index?.models.clip && (
            <div className="smart-row">
              <button className={`chip${smart ? '' : ' on'}`} onClick={() => setSmart(false)}>
                Names &amp; tags
              </button>
              <button className={`chip${smart ? ' on' : ''}`} onClick={() => setSmart(true)}>
                ✨ What&rsquo;s in the photo
              </button>
            </div>
          )}

        </>
      )}

      {/* Faces matching what's typed, the way Google Photos offers them. Tapping
          one opens that person rather than filtering the grid, because "photos of
          Amma" is a place you go, not a filter you sit in. */}
      {searchable && !smart && !!query && <PeopleSuggestions query={query} onOpen={setPerson} />}

      {searchable && <PhotoIndexCard onStatus={setIndex} />}

      {tab === 'albums' ? <><SmartAlbums onCreated={() => setAlbumsRev(n => n + 1)} /><AlbumsGrid key={albumsRev} onOpen={setAlbum} canEdit={canEdit} /></>
        : tab === 'people' ? <PeopleGrid onOpen={setPerson} />
        : tab === 'memories' ? <Memories onOpen={setView} />
        : (
          <PullToRefresh onRefresh={refresh}>
            {loading ? <Spinner />
              : shown.length === 0 ? (
                query
                  ? smart
                    ? <Empty icon="✨" title="Nothing matched that description"
                        hint={index && index.pending.clip >= index.photos
                          ? 'Your photos haven’t been read yet — that runs in the background and can take a few minutes.'
                          : `No photo looks like “${query}”. Try plainer words: cake, beach, car, document, dog.`} />
                    : <Empty icon="🔍" title="No matches" hint={`Nothing found for “${query}”. Try a person's name, an album, a year like 2024, or a month.`} />
                  : isFav
                    ? <Empty icon="★" title="No favourites yet" hint="Tap ☆ on a photo to save it here" />
                    : (
                      <Empty icon="🖼️" title="No photos yet"
                        hint={canEdit ? 'Add a few with Add photos, or back up your phone’s whole gallery at once.' : undefined}
                        action={canEdit ? { label: '☁ Back up my photos', onClick: () => setBackupOpen(true) } : undefined} />
                    )
              )
              : <>
                  <PhotoGrid photos={shown} onOpen={setView} />
                  <InfiniteSentinel onHit={() => load(false)} done={doneRef.current} loading={more}
                    shown={shown.length} total={total} />
                </>}
          </PullToRefresh>
        )}

      {backupOpen && (
        <BackupSheet onClose={() => setBackupOpen(false)}
          onStart={() => { setBackupOpen(false); openPicker(true) }} />
      )}

      {view && <Lightbox photo={view} onClose={() => setView(null)} onFav={() => toggleFav(view)}
        onTrash={() => trash(view)} canEdit={canEdit} />}
    </div>
  )
}

/* ---------- Photo grid + lightbox ---------- */

// Watches a sentinel near the end of the grid; when it scrolls into view it asks
// for the next page. Shows a spinner while loading and a footer once every photo
// is loaded.
function InfiniteSentinel({ onHit, done, loading, shown, total }: {
  onHit: () => void; done: boolean; loading: boolean; shown: number; total: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const cb = useRef(onHit); cb.current = onHit
  useEffect(() => {
    if (done) return
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) cb.current() }, { rootMargin: '800px' })
    io.observe(el)
    return () => io.disconnect()
  }, [done, shown])
  if (done) return <div className="grid-foot">All {total.toLocaleString()} photos loaded</div>
  return (
    <div ref={ref} className="grid-foot">
      <span className="spinner sm" style={{ margin: '14px auto' }} />
      {loading ? `Loading… ${shown.toLocaleString()} of ${total.toLocaleString()}` : ''}
    </div>
  )
}

function PhotoGrid({ photos, onOpen }: { photos: Photo[]; onOpen: (p: Photo) => void }) {
  return (
    <div className="photo-grid">
      {photos.map((p) => (
        <button key={p.id} onClick={() => onOpen(p)} className="thumb">
          <img src={p.thumb_url || p.url} loading="lazy" />
          {!!p.is_favourite && <span className="starred">★</span>}
        </button>
      ))}
    </div>
  )
}

function Lightbox({ photo, onClose, onFav, onTrash, canEdit, albumId, onRemoveFromAlbum }: {
  photo: Photo; onClose: () => void; onFav: () => void; onTrash: () => void; canEdit: boolean
  albumId?: number                    // set when viewing from inside an album
  onRemoveFromAlbum?: () => void
}) {
  const toast = useToast()
  useOverlayBack(onClose) // back gesture / button closes the lightbox
  const [info, setInfo] = useState<PhotoInfo | null>(null)
  const [tagging, setTagging] = useState(false)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [albumPick, setAlbumPick] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [chrome, setChrome] = useState(true)
  const people = info?.people ?? []

  // Download the full-resolution original. The URL is already signed, so no
  // Authorization header is needed — but we still fetch it as a blob so the file
  // saves under a friendly name instead of the opaque stored uuid.
  async function download() {
    setSaving(true)
    try {
      const res = await fetch(photo.url)
      if (!res.ok) throw new Error('fetch')
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      const base = (photo.caption || `photo-${photo.id}`).replace(/[\\/:*?"<>|]+/g, '_')
      a.href = url; a.download = /\.jpe?g$/i.test(base) ? base : `${base}.jpg`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
      toast('Saved to your device')
    } catch { toast('Could not download this photo') }
    finally { setSaving(false) }
  }

  // One request covers the metadata, the people tagged and the albums it's in —
  // the details sheet and the tag row are two views of the same record.
  const loadInfo = useCallback(() => {
    api<PhotoInfo>(`/api/gallery/${photo.id}/info`).then(setInfo).catch(() => setInfo(null))
  }, [photo.id])
  useEffect(() => { loadInfo() }, [loadInfo])

  async function addTag(name: string) {
    await api(`/api/gallery/${photo.id}/tag`, { method: 'POST', body: { name } })
    setTagging(false); loadInfo(); toast(`Tagged ${name}`)
  }
  async function removeTag(pid: number) {
    await api(`/api/gallery/${photo.id}/untag`, { method: 'POST', body: { person_id: pid } })
    loadInfo()
  }

  // Chrome hides on a tap (and whenever you zoom in) so the photo owns the screen —
  // the behaviour people expect from a phone photo viewer.
  const showChrome = chrome && !zoomed

  return (
    <div className="viewer">
      <div className="viewer-stage">
        <Zoomable fill src={photo.url} alt={photo.caption || ''}
          onZoomChange={setZoomed} onSingleTap={() => setChrome((c) => !c)} />
      </div>

      <div className={`viewer-top${showChrome ? '' : ' hidden'}`}>
        <button className="viewer-btn" onClick={onClose} aria-label="Close">✕</button>
        <div className="viewer-title">
          <div className="vt-main">{photo.caption || 'Photo'}</div>
          <div className="vt-sub">{fmtDate(photo.taken_at)}</div>
        </div>
        <button className="viewer-btn" onClick={onFav} aria-label="Favourite">
          {photo.is_favourite ? '★' : '☆'}
        </button>
        <button className="viewer-btn" onClick={() => setDetailsOpen(true)} aria-label="Photo details" title="Details">ⓘ</button>
        <button className="viewer-btn" onClick={download} disabled={saving} aria-label="Download">
          {saving ? '…' : '⤓'}
        </button>
        {canEdit && (
          <button className="viewer-btn danger" onClick={onTrash} aria-label="Move to trash">🗑</button>
        )}
      </div>

      <div className={`viewer-bottom${showChrome ? '' : ' hidden'}`}>
        <div className="tag-row">
          {people.map((p) => (
            <span key={p.id} className="viewer-tag">
              {p.name}
              {canEdit && <button onClick={() => removeTag(p.id)} aria-label={`Remove ${p.name}`}>×</button>}
            </span>
          ))}
          {(info?.albums ?? []).map((a) => (
            <span key={`a${a.id}`} className="viewer-tag album">🗂 {a.name}</span>
          ))}
          {canEdit && <>
            <button className="viewer-tag add" onClick={() => setTagging(true)}>＋ Tag person</button>
            <button className="viewer-tag add" onClick={() => setAlbumPick(true)}>＋ Album</button>
          </>}
          {albumId && canEdit && onRemoveFromAlbum && (
            <button className="viewer-tag remove" onClick={onRemoveFromAlbum}>Remove from album</button>
          )}
        </div>
      </div>

      {tagging && <TagSheet onClose={() => setTagging(false)} onPick={addTag} />}
      {detailsOpen && <DetailsSheet info={info} onClose={() => setDetailsOpen(false)} />}
      {albumPick && (
        <AlbumPickSheet photoIds={[photo.id]} inAlbums={(info?.albums ?? []).map((a) => a.id)}
          onClose={() => setAlbumPick(false)} onDone={loadInfo} />
      )}
    </div>
  )
}

/* ---------- Photo details (EXIF) ---------- */

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="info-row">
      <span className="info-k">{label}</span>
      <span className="info-v">{value}</span>
    </div>
  )
}

function DetailsSheet({ info, onClose }: { info: PhotoInfo | null; onClose: () => void }) {
  if (!info) {
    return <Sheet title="Photo details" onClose={onClose}><Spinner /></Sheet>
  }
  const p = info.photo
  // shot_at is what the camera wrote; taken_at falls back to the upload date when
  // the file arrived without EXIF, so say which one the user is looking at.
  const hasExifDate = !!p.shot_at
  const coords = p.lat != null && p.lon != null
    ? `${p.lat.toFixed(6)}°, ${p.lon.toFixed(6)}°`
    : null
  const noCapture = !p.camera && !p.lens && !coords && !hasExifDate

  return (
    <Sheet title="Photo details" onClose={onClose}>
      <div className="info-list">
        <InfoRow label="Name" value={p.caption || p.orig_name || `Photo ${p.id}`} />
        {!!p.orig_name && p.orig_name !== p.caption && <InfoRow label="File" value={p.orig_name} />}
        <InfoRow label={hasExifDate ? 'Taken' : 'Date'}
          value={hasExifDate ? fmtDateTime(p.shot_at) : `${fmtDate(p.taken_at)} (upload date)`} />
        <InfoRow label={`Added to ${appName()}`} value={fmtDateTime(p.uploaded_at)} />
        <InfoRow label="Size" value={p.size_bytes ? formatBytes(p.size_bytes) : '—'} />
        <InfoRow label="Dimensions" value={p.width && p.height
          ? <>{p.width.toLocaleString()} × {p.height.toLocaleString()}{p.megapixels ? ` · ${p.megapixels} MP` : ''}</>
          : '—'} />
        {!!p.camera && <InfoRow label="Camera" value={p.camera} />}
        {!!p.lens && <InfoRow label="Exposure" value={p.lens} />}
        {coords && <InfoRow label="Location" value={
          <a className="info-link" href={`https://www.google.com/maps/search/?api=1&query=${p.lat},${p.lon}`}
            target="_blank" rel="noreferrer noopener">{coords} ↗</a>
        } />}
        <InfoRow label="Favourite" value={p.is_favourite ? 'Yes ★' : 'No'} />
        <InfoRow label="People" value={info.people.length
          ? info.people.map((x) => x.name).join(', ') : 'None tagged'} />
        <InfoRow label="Albums" value={info.albums.length
          ? info.albums.map((x) => x.name).join(', ') : 'Not in any album'} />
      </div>

      {noCapture && (
        <p className="form-hint" style={{ marginTop: 12 }}>
          No camera or location details were stored with this photo. Phones and chat apps
          usually strip that information when a picture is shared, and screenshots never
          had any — so {appName()} has nothing to show beyond the file itself.
        </p>
      )}
      {coords && (
        <p className="form-hint" style={{ marginTop: 12 }}>
          The location stays on your own server. Nothing is sent anywhere unless you tap
          the coordinates to open a map.
        </p>
      )}
    </Sheet>
  )
}

/** What a whole-gallery backup is about to do, said before it starts.
 *
 *  It asks first because it is not a small action: on a phone this is hundreds
 *  of files and a long upload, and the thing that makes it bearable — that it
 *  carries on in the background while you use the rest of the app — is not
 *  something anyone can guess from a button.
 *
 *  The instruction to use Select All is not padding. A web page cannot read a
 *  phone's photo library by itself, by design; the phone puts up its own picker
 *  and the person has to choose there. Someone expecting the app to find their
 *  photos on its own taps two of them and concludes the backup is broken.
 */
function BackupSheet({ onClose, onStart }: { onClose: () => void; onStart: () => void }) {
  return (
    <Sheet title="Back up my photos" onClose={onClose}>
      <p style={{ color: 'var(--ink-soft)', fontSize: 14, lineHeight: 1.55, marginTop: 4 }}>
        Your phone will ask which photos to allow. Choose <b>Select All</b> to
        back up everything, or pick the ones you want.
      </p>
      <ul style={{ color: 'var(--ink-soft)', fontSize: 14, lineHeight: 1.7,
                   paddingLeft: 18, margin: '12px 0 4px' }}>
        <li>It keeps going in the background — carry on using the app.</li>
        <li>Progress shows at the top of every screen.</li>
        <li>A big selection takes a moment to read before it starts moving.</li>
        <li>Photos already here are skipped, so you can run this again any time.</li>
        <li>Nothing leaves this computer.</li>
      </ul>
      <button className="btn block" style={{ marginTop: 16 }} onClick={onStart}>
        Choose photos
      </button>
      <button className="btn ghost block" style={{ marginTop: 8 }} onClick={onClose}>
        Not now
      </button>
    </Sheet>
  )
}

function TagSheet({ onClose, onPick }: { onClose: () => void; onPick: (name: string) => void }) {
  const [name, setName] = useState('')
  const [people, setPeople] = useState<PersonSummary[]>([])
  useEffect(() => { api<{ people: PersonSummary[] }>('/api/people').then((d) => setPeople(d.people)).catch(() => {}) }, [])
  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Sheet title="Tag a person" onClose={onClose}>
        <Field label="Name">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Type a name" autoFocus />
        </Field>
        {people.length > 0 && <>
          <div className="section-title" style={{ marginTop: 4 }}>Existing people</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
            {people.map((p) => <button key={p.id} className="pill muted" style={{ padding: '9px 12px' }} onClick={() => onPick(p.name)}>{p.name}</button>)}
          </div>
        </>}
        <button className="btn block" disabled={!name.trim()} onClick={() => onPick(name.trim())}>Tag</button>
      </Sheet>
    </div>
  )
}

/* ---------- People ---------- */

/** Face chips for people whose name matches the search term. */
function PeopleSuggestions({ query, onOpen }: {
  query: string; onOpen: (p: PersonSummary) => void
}) {
  const [hits, setHits] = useState<PersonSummary[]>([])

  useEffect(() => {
    let alive = true
    const p = new URLSearchParams({ q: query, limit: '12', min_photos: '1' })
    api<{ people: PersonSummary[] }>(`/api/people?${p}`)
      .then((d) => { if (alive) setHits(d.people) })
      .catch(() => { if (alive) setHits([]) })
    return () => { alive = false }
  }, [query])

  if (!hits.length) return null
  return (
    <div className="ppl-sugg">
      <div className="ppl-sugg-head">People</div>
      <div className="ppl-sugg-row">
        {hits.map((p) => (
          <button key={p.id} className="ppl-chip" onClick={() => onOpen(p)}>
            <span className="ppl-chip-face">
              {p.cover_url ? <img src={p.cover_url} loading="lazy" alt="" /> : <span>🙂</span>}
            </span>
            <span className="ppl-chip-name">{p.name}</span>
            <span className="ppl-chip-n">{p.count}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

const PEOPLE_PAGE = 60

function PeopleGrid({ onOpen }: { onOpen: (p: PersonSummary) => void }) {
  const [people, setPeople] = useState<PersonSummary[] | null>(null)
  const [total, setTotal] = useState(0)
  const [allPeople, setAllPeople] = useState(0)
  // Faces seen in a single photo are usually passers-by, not people you know.
  // Hiding them by default keeps the tab useful on a big library; the toggle
  // below brings them back.
  const [onlyRepeat, setOnlyRepeat] = useState(true)
  const [busy, setBusy] = useState(false)
  const [typed, setTyped] = useState('')
  const [find, setFind] = useState('')
  useEffect(() => {
    const t = window.setTimeout(() => setFind(typed.trim()), 300)
    return () => window.clearTimeout(t)
  }, [typed])

  const load = useCallback(async (offset: number, minPhotos: number, q = '') => {
    setBusy(true)
    try {
      const p = new URLSearchParams({
        offset: String(offset), limit: String(PEOPLE_PAGE), min_photos: String(minPhotos),
      })
      if (q) p.set('q', q)
      const d = await api<{ people: PersonSummary[]; total: number; all_people: number }>(
        `/api/people?${p}`)
      setPeople((prev) => (offset ? [...(prev ?? []), ...d.people] : d.people))
      setTotal(d.total)
      setAllPeople(d.all_people)
    } catch { setPeople([]) }
    finally { setBusy(false) }
  }, [])

  // Searching a name should look through everyone, not just repeat faces.
  useEffect(() => { load(0, find ? 0 : (onlyRepeat ? 2 : 1), find) }, [load, onlyRepeat, find])

  if (!people) return <Spinner />

  const hiddenSingles = allPeople - total
  if (!people.length) {
    if (find) {
      return <Empty icon="🙂" title="No one by that name"
        hint={`Nobody is named “${find}”. Open a face and tap Rename to give it a name.`} />
    }
    return onlyRepeat && allPeople > 0
      ? <Empty icon="🙂" title="Nobody appears twice yet"
          hint={`${allPeople.toLocaleString()} faces were found, each in a single photo. Tap “Show everyone” to see them.`} />
      : <Empty icon="🙂" title="No people yet"
          hint="Faces are grouped automatically as photos are read — see the progress above." />
  }

  return (
    <div>
      {allPeople > 0 && (
        <div className="searchbar">
          <span className="searchbar-ic" aria-hidden="true">🔍</span>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} type="search"
            autoComplete="off" placeholder="Search people by name" aria-label="Search people" />
          {!!typed && <button className="searchbar-x" onClick={() => setTyped('')} aria-label="Clear">✕</button>}
        </div>
      )}

      {allPeople > 0 && !find && (
        <div className="smart-row">
          <button className={`chip${onlyRepeat ? ' on' : ''}`} onClick={() => setOnlyRepeat(true)}>
            Seen more than once
          </button>
          <button className={`chip${onlyRepeat ? '' : ' on'}`} onClick={() => setOnlyRepeat(false)}>
            Show everyone ({allPeople.toLocaleString()})
          </button>
        </div>
      )}

      <div className="people-grid">
        {people.map((p) => (
          <button key={p.id} className="person" onClick={() => onOpen(p)}>
            <div className="person-cover">
              {/* lazy: a page of 60 covers must not fire 60 requests at once */}
              {p.cover_url ? <img src={p.cover_url} loading="lazy" alt="" /> : <span>🙂</span>}
            </div>
            <div className="person-name">{p.name}</div>
            <div className="person-count">{p.count} photo{p.count === 1 ? '' : 's'}</div>
          </button>
        ))}
      </div>

      {people.length < total && (
        <button className="btn ghost block" style={{ marginTop: 14 }} disabled={busy}
          onClick={() => load(people.length, find ? 0 : (onlyRepeat ? 2 : 1), find)}>
          {busy ? 'Loading…' : `Show more (${(total - people.length).toLocaleString()} left)`}
        </button>
      )}
      {onlyRepeat && hiddenSingles > 0 && people.length >= total && (
        <p className="muted" style={{ fontSize: 11.5, textAlign: 'center', marginTop: 10 }}>
          {hiddenSingles.toLocaleString()} more face{hiddenSingles === 1 ? '' : 's'} seen in only one photo.
        </p>
      )}
    </div>
  )
}

function PersonView({ person, onBack, onOpen, view, setView, toggleFav, trash, canEdit }: {
  person: PersonSummary; onBack: () => void; onOpen: (p: Photo) => void
  view: Photo | null; setView: (p: Photo | null) => void
  toggleFav: (p: Photo) => void; trash: (p: Photo) => void; canEdit: boolean
}) {
  const toast = useToast()
  const [items, setItems] = useState<Photo[] | null>(null)
  const [name, setName] = useState(person.name)
  const [renaming, setRenaming] = useState(false)

  const load = useCallback(() => {
    api<{ items: Photo[] }>(`/api/people/${person.id}/photos`).then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [person.id])
  useEffect(() => { load() }, [load])

  async function rename(v: string) {
    await api(`/api/people/${person.id}`, { method: 'PUT', body: { name: v } })
    setName(v); setRenaming(false); toast('Renamed')
  }

  return (
    <div className="screen">
      <TopBar title={name} sub={`${items?.length ?? person.count} photos`} onBack={onBack}
        right={canEdit ? <button className="btn ghost sm" onClick={() => setRenaming(true)}>Rename</button> : undefined} />
      {!items ? <Spinner /> : items.length === 0 ? <Empty icon="🙂" title="No photos" /> : <PhotoGrid photos={items} onOpen={onOpen} />}
      {renaming && <RenameSheet initial={name} onClose={() => setRenaming(false)} onSave={rename} />}
      {view && <Lightbox photo={view} onClose={() => setView(null)} onFav={() => toggleFav(view)} onTrash={() => { trash(view); load() }} canEdit={canEdit} />}
    </div>
  )
}

function RenameSheet({ initial, onClose, onSave }: { initial: string; onClose: () => void; onSave: (v: string) => void }) {
  const [v, setV] = useState(initial)
  return (
    <Sheet title="Rename person" onClose={onClose}>
      <Field label="Name"><input className="input" value={v} onChange={(e) => setV(e.target.value)} autoFocus /></Field>
      <button className="btn block" disabled={!v.trim()} onClick={() => onSave(v.trim())}>Save</button>
    </Sheet>
  )
}

/* ---------- Albums ---------- */

function AlbumsGrid({ onOpen, canEdit }: {
  onOpen: (a: AlbumSummary) => void; canEdit: boolean
}) {
  const toast = useToast()
  const [albums, setAlbums] = useState<AlbumSummary[] | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(() => {
    api<{ albums: AlbumSummary[] }>('/api/gallery/albums')
      .then((d) => setAlbums(d.albums)).catch(() => setAlbums([]))
  }, [])
  useEffect(() => { load() }, [load])

  async function create(name: string) {
    try {
      await api('/api/gallery/albums', { method: 'POST', body: { name } })
      setCreating(false); load(); toast(`Album “${name}” created`)
    } catch (e) { toast(errorMessage(e, 'Could not create the album')) }
  }

  if (!albums) return <Spinner />
  return (
    <div>
      {canEdit && (
        <button className="btn ghost block" style={{ marginBottom: 14 }} onClick={() => setCreating(true)}>
          ＋ New album
        </button>
      )}
      {albums.length === 0
        ? <Empty icon="🗂️" title="No albums yet"
            hint={canEdit ? 'Group photos into albums — a holiday, a project, receipts for the year.' : undefined} />
        : (
          <div className="album-grid">
            {albums.map((a) => (
              <button key={a.id} className="album" onClick={() => onOpen(a)}>
                <div className="album-cover">
                  {a.cover_url ? <img src={a.cover_url} loading="lazy" alt="" /> : <span>🗂️</span>}
                </div>
                <div className="album-name">{a.name}</div>
                <div className="album-count">{a.count} photo{a.count === 1 ? '' : 's'}</div>
              </button>
            ))}
          </div>
        )}
      {creating && <NameSheet title="New album" label="Album name" cta="Create"
        placeholder="Goa 2025, Receipts, Family…" onClose={() => setCreating(false)} onSave={create} />}
    </div>
  )
}

/** Shared create/rename sheet — an album name is the only field either one needs. */
function NameSheet({ title, label, cta, initial = '', placeholder, onClose, onSave }: {
  title: string; label: string; cta: string; initial?: string; placeholder?: string
  onClose: () => void; onSave: (v: string) => void | Promise<void>
}) {
  const [v, setV] = useState(initial)
  const [busy, setBusy] = useState(false)
  const name = v.trim()
  const problem = !name ? 'Enter a name' : name.length > 120 ? 'Keep it under 120 characters' : ''
  async function submit() {
    if (problem || busy) return
    setBusy(true)
    try { await onSave(name) } finally { setBusy(false) }
  }
  return (
    <Sheet title={title} onClose={onClose}>
      <Field label={label}>
        <input className="input" value={v} maxLength={120} autoFocus placeholder={placeholder}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
      </Field>
      {!!v && !!problem && <p className="form-hint err">{problem}</p>}
      <button className="btn block" disabled={!!problem || busy} onClick={submit}>
        {busy ? 'Saving…' : cta}
      </button>
    </Sheet>
  )
}

function AlbumView({ album, onBack, canEdit }: {
  album: AlbumSummary; onBack: () => void; canEdit: boolean
}) {
  const toast = useToast()
  const [items, setItems] = useState<Photo[] | null>(null)
  const [name, setName] = useState(album.name)
  const [view, setView] = useState<Photo | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [adding, setAdding] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)

  const load = useCallback(() => {
    api<{ items: Photo[] }>(`/api/gallery?album=${album.id}&limit=300`)
      .then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [album.id])
  useEffect(() => { load() }, [load])

  async function rename(v: string) {
    try {
      await api(`/api/gallery/albums/${album.id}`, { method: 'PUT', body: { name: v } })
      setName(v); setRenaming(false); toast('Album renamed')
    } catch (e) { toast(errorMessage(e, 'Could not rename the album')) }
  }

  async function destroy() {
    try {
      await api(`/api/gallery/albums/${album.id}`, { method: 'DELETE' })
      toast('Album deleted — the photos are still in your gallery')
      onBack()
    } catch (e) { toast(errorMessage(e, 'Could not delete the album')); setConfirmDel(false) }
  }

  async function addPhotos(ids: number[]) {
    if (!ids.length) { setAdding(false); return }
    try {
      const r = await api<{ added: number }>(`/api/gallery/albums/${album.id}/photos`,
        { method: 'POST', body: { photo_ids: ids } })
      setAdding(false); load()
      toast(`Added ${r.added} photo${r.added === 1 ? '' : 's'}`)
    } catch (e) { toast(errorMessage(e, 'Could not add those photos')) }
  }

  async function removeFromAlbum(p: Photo) {
    try {
      await api(`/api/gallery/albums/${album.id}/remove`, { method: 'POST', body: { photo_ids: [p.id] } })
      setItems((xs) => xs?.filter((x) => x.id !== p.id) ?? null)
      setView(null); toast('Removed from album')
    } catch (e) { toast(errorMessage(e, 'Could not remove it')) }
  }

  async function toggleFav(p: Photo) {
    await api(`/api/gallery/${p.id}/favourite`, { method: 'POST' })
    setItems((xs) => xs?.map((x) => x.id === p.id ? { ...x, is_favourite: x.is_favourite ? 0 : 1 } : x) ?? null)
    setView((v) => v && v.id === p.id ? { ...v, is_favourite: v.is_favourite ? 0 : 1 } : v)
  }

  async function trash(p: Photo) {
    await api(`/api/gallery/${p.id}`, { method: 'DELETE' })
    setItems((xs) => xs?.filter((x) => x.id !== p.id) ?? null)
    setView(null); toast('Moved to trash')
  }

  const count = items?.length ?? album.count
  return (
    <div className="screen">
      <TopBar title={name} sub={`${count} photo${count === 1 ? '' : 's'}`} onBack={onBack}
        right={canEdit ? (
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn ghost sm" onClick={() => setRenaming(true)}>Rename</button>
            <button className="btn sm" onClick={() => setAdding(true)}>＋ Add</button>
          </div>
        ) : undefined} />

      {!items ? <Spinner />
        : items.length === 0
          ? <Empty icon="🗂️" title="This album is empty"
              hint={canEdit ? 'Tap ＋ Add to put photos in it.' : undefined} />
          : <PhotoGrid photos={items} onOpen={setView} />}

      {canEdit && (
        <button className="btn danger block" style={{ marginTop: 24 }} onClick={() => setConfirmDel(true)}>
          Delete album
        </button>
      )}

      {renaming && <NameSheet title="Rename album" label="Album name" cta="Save" initial={name}
        onClose={() => setRenaming(false)} onSave={rename} />}
      {adding && <PhotoPicker exclude={new Set((items ?? []).map((p) => p.id))}
        onClose={() => setAdding(false)} onAdd={addPhotos} />}
      {confirmDel && (
        <Sheet title="Delete this album?" onClose={() => setConfirmDel(false)}>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16 }}>
            “{name}” will be removed. The <b>{count} photo{count === 1 ? '' : 's'} stay in your
            gallery</b> — only the album disappears.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirmDel(false)}>Cancel</button>
            <button className="btn danger block" onClick={destroy}>Delete album</button>
          </div>
        </Sheet>
      )}
      {view && <Lightbox photo={view} onClose={() => setView(null)} onFav={() => toggleFav(view)}
        onTrash={() => trash(view)} canEdit={canEdit}
        albumId={album.id} onRemoveFromAlbum={() => removeFromAlbum(view)} />}
    </div>
  )
}

/** Multi-select grid for choosing photos to add to an album. */
function PhotoPicker({ exclude, onClose, onAdd }: {
  exclude: Set<number>; onClose: () => void; onAdd: (ids: number[]) => void
}) {
  const [items, setItems] = useState<Photo[] | null>(null)
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState('')
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(typed.trim()), 300)
    return () => window.clearTimeout(t)
  }, [typed])

  useEffect(() => {
    setItems(null)
    const p = new URLSearchParams({ limit: '300' })
    if (query) p.set('q', query)
    api<{ items: Photo[] }>(`/api/gallery?${p}`)
      .then((d) => setItems(d.items.filter((x) => !exclude.has(x.id))))
      .catch(() => setItems([]))
  }, [query]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (id: number) => setSel((s) => {
    const n = new Set(s)
    n.has(id) ? n.delete(id) : n.add(id)
    return n
  })

  return (
    <Sheet title="Add photos" onClose={onClose}>
      <div className="searchbar" style={{ marginBottom: 12 }}>
        <span className="searchbar-ic" aria-hidden="true">🔍</span>
        <input value={typed} onChange={(e) => setTyped(e.target.value)} type="search"
          autoComplete="off" placeholder="Search your photos" aria-label="Search photos" />
        {!!typed && <button className="searchbar-x" onClick={() => setTyped('')} aria-label="Clear search">✕</button>}
      </div>

      <div className="pick-scroll">
        {!items ? <Spinner />
          : items.length === 0
            ? <Empty icon="🖼️" title={query ? 'No matches' : 'Nothing left to add'}
                hint={query ? undefined : 'Every photo is already in this album.'} />
            : (
              <div className="photo-grid">
                {items.map((p) => (
                  <button key={p.id} className={`thumb pick${sel.has(p.id) ? ' on' : ''}`} onClick={() => toggle(p.id)}>
                    <img src={p.thumb_url || p.url} loading="lazy" alt="" />
                    <span className="pick-tick">{sel.has(p.id) ? '✓' : ''}</span>
                  </button>
                ))}
              </div>
            )}
      </div>

      <button className="btn block" style={{ marginTop: 14 }} disabled={!sel.size}
        onClick={() => onAdd([...sel])}>
        {sel.size ? `Add ${sel.size} photo${sel.size === 1 ? '' : 's'}` : 'Select photos to add'}
      </button>
    </Sheet>
  )
}

/** Put the given photo(s) into an album — or into a brand-new one. */
function AlbumPickSheet({ photoIds, inAlbums, onClose, onDone }: {
  photoIds: number[]; inAlbums: number[]; onClose: () => void; onDone: () => void
}) {
  const toast = useToast()
  const [albums, setAlbums] = useState<AlbumSummary[] | null>(null)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(0)
  const member = new Set(inAlbums)

  const load = useCallback(() => {
    api<{ albums: AlbumSummary[] }>('/api/gallery/albums')
      .then((d) => setAlbums(d.albums)).catch(() => setAlbums([]))
  }, [])
  useEffect(() => { load() }, [load])

  async function toggle(a: AlbumSummary) {
    setBusy(a.id)
    const path = member.has(a.id) ? `/api/gallery/albums/${a.id}/remove` : `/api/gallery/albums/${a.id}/photos`
    try {
      await api(path, { method: 'POST', body: { photo_ids: photoIds } })
      onDone(); load()
      toast(member.has(a.id) ? `Removed from ${a.name}` : `Added to ${a.name}`)
    } catch (e) { toast(errorMessage(e, 'Could not update the album')) }
    finally { setBusy(0) }
  }

  async function create(name: string) {
    try {
      await api('/api/gallery/albums', { method: 'POST', body: { name, photo_ids: photoIds } })
      setCreating(false); onDone(); load(); toast(`Added to new album “${name}”`)
    } catch (e) { toast(errorMessage(e, 'Could not create the album')) }
  }

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Sheet title="Add to album" onClose={onClose}>
        <button className="btn ghost block" style={{ marginBottom: 12 }} onClick={() => setCreating(true)}>
          ＋ New album
        </button>
        {!albums ? <Spinner />
          : albums.length === 0
            ? <p className="muted" style={{ fontSize: 13.5 }}>You don’t have any albums yet — create your first one above.</p>
            : (
              <div className="list">
                {albums.map((a) => (
                  <button key={a.id} className="card album-row" disabled={busy === a.id} onClick={() => toggle(a)}>
                    <div className="album-row-cover">
                      {a.cover_url ? <img src={a.cover_url} alt="" /> : <span>🗂️</span>}
                    </div>
                    <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                      <div style={{ fontWeight: 700 }}>{a.name}</div>
                      <div className="sub" style={{ fontSize: 12 }}>{a.count} photo{a.count === 1 ? '' : 's'}</div>
                    </div>
                    <span className={`album-check${member.has(a.id) ? ' on' : ''}`}>
                      {busy === a.id ? '…' : member.has(a.id) ? '✓' : '＋'}
                    </span>
                  </button>
                ))}
              </div>
            )}
        {creating && <NameSheet title="New album" label="Album name" cta="Create"
          placeholder="Goa 2025, Receipts, Family…" onClose={() => setCreating(false)} onSave={create} />}
      </Sheet>
    </div>
  )
}

/* ---------- Trash ---------- */

function TrashView({ onBack, canEdit }: { onBack: () => void; canEdit: boolean }) {
  const toast = useToast()
  const [items, setItems] = useState<Photo[] | null>(null)
  const [confirm, setConfirm] = useState<Photo | null>(null)
  const [emptyOpen, setEmptyOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<{ items: Photo[] }>('/api/gallery/trash').then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [])
  useEffect(() => { load() }, [load])

  async function restore(p: Photo) {
    await api(`/api/gallery/${p.id}/restore`, { method: 'POST' })
    setItems((xs) => xs?.filter((x) => x.id !== p.id) ?? null); toast('Restored')
  }
  async function destroy(p: Photo) {
    await api(`/api/gallery/${p.id}/permanent`, { method: 'DELETE' })
    setItems((xs) => xs?.filter((x) => x.id !== p.id) ?? null); setConfirm(null); toast('Deleted forever')
  }
  async function emptyTrash() {
    setBusy(true)
    try {
      const r = await api<{ deleted: number }>('/api/gallery/trash/empty', { method: 'POST' })
      setItems([]); setEmptyOpen(false)
      toast(`Deleted ${r.deleted} photo${r.deleted === 1 ? '' : 's'} forever`)
    } catch { toast('Could not empty trash') }
    finally { setBusy(false) }
  }

  const count = items?.length ?? 0
  const emptyBtn = canEdit && count > 0
    ? <button className="btn danger sm" onClick={() => setEmptyOpen(true)}>Empty</button>
    : undefined

  return (
    <div className="screen">
      <TopBar title="Trash" sub={items ? `${count} item${count === 1 ? '' : 's'}` : undefined} onBack={onBack} right={emptyBtn} />
      {!items ? <Spinner /> : items.length === 0 ? <Empty icon="🗑️" title="Trash is empty" hint="Photos you delete land here first" /> : (
        <>
          <p className="muted" style={{ fontSize: 12.5, margin: '0 2px 12px' }}>Restore photos to your gallery, or delete them forever.</p>
          <div className="list">
            {items.map((p) => (
              <div key={p.id} className="card" style={{ padding: 12 }}>
                <div className="rowitem">
                  <img src={p.thumb_url || p.url} className="trash-thumb" />
                  <div className="main">
                    <div className="t">{p.caption || 'Photo'}</div>
                    <div className="s">{fmtDate(p.taken_at)}</div>
                  </div>
                </div>
                {canEdit && (
                  <div className="swipe-actions">
                    <button className="btn ghost sm" style={{ flex: 1 }} onClick={() => restore(p)}>↩ Restore</button>
                    <button className="btn danger sm" style={{ flex: 1 }} onClick={() => setConfirm(p)}>Delete forever</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
      {confirm && (
        <Sheet title="Delete forever?" onClose={() => setConfirm(null)}>
          <div className="rowitem" style={{ marginBottom: 16 }}>
            <img src={confirm.thumb_url || confirm.url} className="trash-thumb" />
            <div className="main"><div className="t">{confirm.caption || 'Photo'}</div><div className="s">{fmtDate(confirm.taken_at)}</div></div>
          </div>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16 }}>This permanently removes the photo and its file. This can’t be undone.</p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirm(null)}>Cancel</button>
            <button className="btn danger block" onClick={() => destroy(confirm)}>Delete forever</button>
          </div>
        </Sheet>
      )}
      {emptyOpen && (
        <Sheet title="Empty trash?" onClose={() => setEmptyOpen(false)}>
          <div style={{ textAlign: 'center', fontSize: 40, marginBottom: 8 }}>🗑️</div>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16, textAlign: 'center' }}>
            This permanently deletes all <b>{count}</b> photo{count === 1 ? '' : 's'} in Trash and their files.
            This <b>can’t be undone</b>.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setEmptyOpen(false)}>Cancel</button>
            <button className="btn danger block" disabled={busy} onClick={emptyTrash}>{busy ? 'Deleting…' : `Delete all ${count}`}</button>
          </div>
        </Sheet>
      )}
    </div>
  )
}

/* ---------- Duplicates ---------- */

function IcDuplicate({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round">
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M4 16V6a2 2 0 0 1 2-2h10" />
    </svg>
  )
}

type DupMode = 'exact' | 'similar'
const STRICTNESS = [
  { key: 0, label: 'Identical look' },
  { key: 2, label: 'Very similar' },
  { key: 4, label: 'Similar' },
]

function DuplicatesView({ onBack, canEdit, initialMode = 'exact' }: {
  onBack: (changed: boolean) => void; canEdit: boolean; initialMode?: DupMode
}) {
  const toast = useToast()
  useOverlayBack(() => onBack(false))
  const [mode, setMode] = useState<DupMode>(initialMode)
  const [dist, setDist] = useState(2) // perceptual strictness for 'similar'
  const [data, setData] = useState<DuplicatesData | null>(null)
  const [keep, setKeep] = useState<Record<string, number>>({}) // hash -> id to keep
  const [skip, setSkip] = useState<Set<string>>(new Set())     // hashes to leave untouched
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const changed = useRef(false)
  const isSimilar = mode === 'similar'
  const noun = isSimilar ? 'similar' : 'duplicate'

  const load = useCallback(() => {
    setData(null)
    const url = mode === 'similar' ? `/api/gallery/similar?distance=${dist}` : '/api/gallery/duplicates'
    api<DuplicatesData>(url)
      .then((d) => { setData(d); setKeep(Object.fromEntries(d.groups.map((g) => [g.hash, g.keep_id]))); setSkip(new Set()) })
      .catch(() => setData({ groups: [], group_count: 0, extra: 0 }))
  }, [mode, dist])
  useEffect(() => { load() }, [load])

  const deleteIds = (): number[] => {
    if (!data) return []
    const ids: number[] = []
    for (const g of data.groups) {
      if (skip.has(g.hash)) continue
      const keepId = keep[g.hash] ?? g.keep_id
      for (const it of g.items) if (it.id !== keepId) ids.push(it.id)
    }
    return ids
  }
  const delCount = deleteIds().length

  async function resolve() {
    const ids = deleteIds()
    if (!ids.length) return
    setBusy(true)
    try {
      const r = await api<{ trashed: number }>('/api/gallery/duplicates/resolve', { method: 'POST', body: { delete_ids: ids } })
      changed.current = true
      toast(`Moved ${r.trashed} photo${r.trashed === 1 ? '' : 's'} to trash`)
      setConfirm(false)
      load() // re-scan; should now be clean
    } catch { toast('Could not remove photos') }
    finally { setBusy(false) }
  }

  return (
    <div className="screen">
      <TopBar title="Find duplicates" onBack={() => onBack(changed.current)}
        sub={data ? `${data.group_count} set${data.group_count === 1 ? '' : 's'}` : undefined} />

      {/* mode switch: exact byte-copies vs perceptual look-alikes */}
      <div className="seg4 dup-modes">
        <button className={mode === 'exact' ? 'on' : ''} onClick={() => setMode('exact')}>Exact copies</button>
        <button className={mode === 'similar' ? 'on' : ''} onClick={() => setMode('similar')}>Similar photos</button>
      </div>
      {isSimilar && (
        <div className="dup-strict">
          {STRICTNESS.map((s) => (
            <button key={s.key} className={`chip${dist === s.key ? ' on' : ''}`} onClick={() => setDist(s.key)}>{s.label}</button>
          ))}
        </div>
      )}

      {!data ? <div><Spinner />{isSimilar && <p className="muted" style={{ textAlign: 'center', fontSize: 12.5 }}>Analysing photos…</p>}</div>
        : data.groups.length === 0
          ? <Empty icon="🎉" title={isSimilar ? 'No similar photos' : 'No duplicates'}
              hint={isSimilar ? 'No near-duplicate look-alikes at this strictness.' : 'Every photo in your gallery is unique.'} />
          : (
            <>
              <div className="dup-hero card">
                <div className="dup-hero-n">{data.extra}</div>
                <div className="dup-hero-txt">
                  <b>{data.extra} {noun} photo{data.extra === 1 ? '' : 's'}</b> across {data.group_count} set{data.group_count === 1 ? '' : 's'}.
                  <span className="muted"> {isSimilar
                    ? 'Look-alikes — resized, re-saved or lightly edited copies. Review each before removing.'
                    : 'One copy from each set is kept — tap a photo to choose which.'}</span>
                  {isSimilar && !!data.skipped && <span className="muted"> ({data.skipped} large group{data.skipped === 1 ? '' : 's'} skipped for safety.)</span>}
                </div>
              </div>

              <div className="dup-list">
                {data.groups.map((g) => (
                  <DupGroup key={g.hash} g={g} noun={noun} keepId={keep[g.hash] ?? g.keep_id} skipped={skip.has(g.hash)}
                    onKeep={(id) => setKeep((m) => ({ ...m, [g.hash]: id }))}
                    onToggleSkip={() => setSkip((s) => { const n = new Set(s); n.has(g.hash) ? n.delete(g.hash) : n.add(g.hash); return n })} />
                ))}
              </div>
              {canEdit && (
                <div className="dup-actionbar">
                  <div className="dup-count">{delCount ? `${delCount} to remove` : 'Nothing selected'}</div>
                  <button className="btn danger" disabled={!delCount || busy} onClick={() => setConfirm(true)}>
                    Move {delCount || ''} to Trash
                  </button>
                </div>
              )}
            </>
          )}

      {confirm && (
        <Sheet title="Move to trash?" onClose={() => setConfirm(false)}>
          <p className="muted" style={{ fontSize: 13.5, marginBottom: 16 }}>
            {delCount} {noun} photo{delCount === 1 ? '' : 's'} will be moved to Trash, keeping one copy of each set.
            You can restore them from Trash anytime.
          </p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn ghost block" onClick={() => setConfirm(false)}>Cancel</button>
            <button className="btn danger block" disabled={busy} onClick={resolve}>{busy ? 'Removing…' : `Move ${delCount} to Trash`}</button>
          </div>
        </Sheet>
      )}
    </div>
  )
}

function DupGroup({ g, noun, keepId, skipped, onKeep, onToggleSkip }: {
  g: DuplicateGroup; noun: string; keepId: number; skipped: boolean
  onKeep: (id: number) => void; onToggleSkip: () => void
}) {
  return (
    <div className={`dup-group card${skipped ? ' skipped' : ''}`}>
      <div className="dup-group-head">
        <span>{g.count} {noun === 'similar' ? 'similar' : 'copies'}</span>
        <button className="dup-skip" onClick={onToggleSkip}>{skipped ? 'Include' : 'Skip set'}</button>
      </div>
      <div className="dup-thumbs">
        {g.items.map((p) => {
          const isKeep = p.id === keepId
          return (
            <button key={p.id} className={`dup-thumb${!skipped && isKeep ? ' keep' : ''}${!skipped && !isKeep ? ' del' : ''}`}
              onClick={() => onKeep(p.id)} title={isKeep ? 'Kept' : 'Tap to keep this one'}>
              <img src={p.thumb_url || p.url} loading="lazy" />
              {!skipped && (isKeep
                ? <span className="dup-badge keep">✓ Keep</span>
                : <span className="dup-badge del">Remove</span>)}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* ---------- Memories ---------- */

function Memories({ onOpen }: { onOpen: (p: Photo) => void }) {
  const [data, setData] = useState<{ groups: MemoryGroup[]; date: string } | null>(null)
  useEffect(() => { api<{ groups: MemoryGroup[]; date: string }>('/api/gallery/memories').then(setData).catch(() => setData({ groups: [], date: '' })) }, [])
  if (!data) return <Spinner />
  if (!data.groups.length) return <Empty icon="🕰️" title="No memories today" hint="Photos from this day in past years will appear here" />
  return (
    <div>
      <div className="card" style={{ background: 'linear-gradient(135deg,#f43f5e,#f59e0b)', color: '#fff', marginBottom: 14 }}>
        <div style={{ fontSize: 13, opacity: 0.9 }}>On this day · {data.date}</div>
        <div style={{ fontSize: 20, fontWeight: 800 }}>Your memories 🎞️</div>
      </div>
      {data.groups.map((g) => (
        <div key={g.years}>
          <div className="section-title">{g.label}</div>
          <PhotoGrid photos={g.items} onOpen={onOpen} />
        </div>
      ))}
    </div>
  )
}
