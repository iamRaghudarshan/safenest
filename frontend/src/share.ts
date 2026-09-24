/** Hand a file to the device, so the device can hand it to anything.
 *
 *  WHAT THIS IS. On a phone, `navigator.share` opens the system share sheet —
 *  the one with WhatsApp, Telegram, Mail, AirDrop and "Save to Photos" in it.
 *  That single call covers everything somebody means by "share": sending it to
 *  a person, posting it, and saving it to the camera roll are all just entries
 *  in that sheet, chosen by them rather than by us.
 *
 *  WHY IT IS NOT A LINK. Nothing here uploads, publishes or creates a URL. The
 *  file goes from this machine to the device's own share sheet and no further,
 *  which keeps the product's promise intact: the photo never sits on an
 *  address that somebody else could find.
 *
 *  THE FALLBACK IS NOT A CONSOLATION PRIZE. On a desktop browser, or an
 *  older one, there is no share sheet — so the file downloads, which is what
 *  "share" means on a desktop anyway: you save it and then attach it.
 */

/** Does this browser have a share sheet that accepts files at all?
 *
 *  Checked with a probe File rather than by sniffing the user agent. Several
 *  browsers define `navigator.share` for TEXT and reject files, so testing
 *  for the function alone offers a button that fails on use.
 */
export function canShareFiles(): boolean {
  try {
    if (typeof navigator === 'undefined' || !navigator.share || !navigator.canShare) {
      return false
    }
    const probe = new File([new Uint8Array([1])], 'probe.jpg', { type: 'image/jpeg' })
    return navigator.canShare({ files: [probe] })
  } catch {
    return false
  }
}

export type ShareResult = 'shared' | 'downloaded' | 'cancelled'

/** Extensions for the types this app actually stores. */
const EXT_FOR_TYPE: Record<string, string> = {
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
  'image/gif': 'gif', 'image/heic': 'heic', 'image/avif': 'avif',
  'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm',
  'application/pdf': 'pdf', 'application/zip': 'zip',
}

/** Make the filename agree with what the bytes ACTUALLY are.
 *
 *  THE BUG THIS FIXES. The caller knows what it MEANT to share and guesses an
 *  extension from that — "a photo, so .jpg". But a moving highlight is stored
 *  as an animated WebP, so the share sheet was handed `That day.jpg`
 *  containing WebP. Some apps refuse a file whose extension and content
 *  disagree, and others send it and let the recipient's phone refuse it,
 *  which is worse because nobody finds out.
 *
 *  The blob's own type is authoritative — it comes from the server's
 *  Content-Type, which is derived from the real file — so it wins over the
 *  guess whenever it says something definite.
 */
function agreeExtension(filename: string, type: string): string {
  const want = EXT_FOR_TYPE[(type || '').split(';')[0].trim().toLowerCase()]
  if (!want) return filename                 // unknown type: leave it alone
  const dot = filename.lastIndexOf('.')
  const have = dot > 0 ? filename.slice(dot + 1).toLowerCase() : ''
  if (have === want) return filename
  // jpeg/jpg are the same thing and swapping them helps nobody.
  if ((have === 'jpeg' && want === 'jpg') || (have === 'jpg' && want === 'jpeg')) {
    return filename
  }
  return (dot > 0 ? filename.slice(0, dot) : filename) + '.' + want
}

function saveAs(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoked on a delay, not immediately: Safari has not started the download
  // when click() returns, and revoking first cancels it.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Fetch a file and offer it to the device. Never throws for an ordinary
 *  outcome — cancelling is not an error, and neither is a browser without a
 *  share sheet.
 *
 *  @param url       where to get the bytes. Signed media URLs need no header;
 *                   anything else is given the bearer token by the caller.
 *  @param filename  what the file should be called wherever it lands.
 */
export async function shareFile(
  url: string,
  filename: string,
  opts: { headers?: Record<string, string>; title?: string; text?: string } = {},
): Promise<ShareResult> {
  const res = await fetch(url, { headers: opts.headers })
  if (!res.ok) throw new Error('Could not read that file')
  const blob = await res.blob()

  const name = agreeExtension(filename, blob.type)
  if (canShareFiles()) {
    const file = new File([blob], name, {
      type: blob.type || 'application/octet-stream',
    })
    try {
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: opts.title, text: opts.text })
        return 'shared'
      }
    } catch (err) {
      const name = (err as { name?: string })?.name
      // AbortError is somebody closing the sheet. Treating that as a failure
      // would put an error toast on screen for a deliberate act.
      if (name === 'AbortError') return 'cancelled'
      // NotAllowedError is iOS Safari refusing because the gesture that
      // started this expired while the file was being fetched. There is no
      // way to fetch first AND keep the gesture, so the honest answer is to
      // fall through and save the file instead of showing an error nobody
      // can act on.
      if (name !== 'NotAllowedError') {
        console.warn('[share] the share sheet refused, saving instead:', err)
      }
    }
  }

  saveAs(blob, name)
  return 'downloaded'
}

/** Several files at once, to one share sheet.
 *
 *  This is what "send these to WhatsApp" actually is: five photos in one
 *  message, not five downloads to attach by hand.
 *
 *  Capped, and the cap is not arbitrary. Every file is held in memory as a
 *  Blob before the sheet opens, and phones kill a tab that asks for too much
 *  far more readily than they warn it — a share of two hundred photos would
 *  fail as a blank crash rather than as an error.
 */
export const MAX_SHARE_FILES = 20

export async function shareFiles(
  items: { url: string; filename: string; headers?: Record<string, string> }[],
  opts: { title?: string } = {},
): Promise<ShareResult> {
  const take = items.slice(0, MAX_SHARE_FILES)
  if (!take.length) return 'cancelled'
  if (take.length === 1) {
    return shareFile(take[0].url, take[0].filename,
                     { headers: take[0].headers, title: opts.title })
  }

  const files: File[] = []
  for (const it of take) {
    const res = await fetch(it.url, { headers: it.headers })
    if (!res.ok) continue
    const blob = await res.blob()
    files.push(new File([blob], agreeExtension(it.filename, blob.type),
                        { type: blob.type || 'application/octet-stream' }))
  }
  if (!files.length) throw new Error('None of those files could be read')

  if (canShareFiles()) {
    try {
      if (navigator.canShare({ files })) {
        await navigator.share({ files, title: opts.title })
        return 'shared'
      }
    } catch (err) {
      const name = (err as { name?: string })?.name
      if (name === 'AbortError') return 'cancelled'
      if (name !== 'NotAllowedError') {
        console.warn('[share] the share sheet refused, saving instead:', err)
      }
    }
  }

  // No sheet: save them. One at a time and spaced out, because a browser
  // treats a burst of downloads as a popup attack and silently drops all but
  // the first.
  for (let i = 0; i < files.length; i++) {
    saveAs(files[i], files[i].name)
    if (i < files.length - 1) await new Promise((r) => setTimeout(r, 350))
  }
  return 'downloaded'
}


/** A filename somebody would recognise in their downloads folder.
 *
 *  Every character Windows, macOS and Android disagree about is replaced, and
 *  the extension is kept — a share sheet decides what an item IS from its
 *  extension and its type, and a photo called "Holiday" with neither offers
 *  no apps to open it.
 */
export function safeFilename(label: string, ext: string, fallback = 'file'): string {
  const base = (label || '')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80) || fallback
  const e = (ext || '').replace(/[^a-z0-9]/gi, '').toLowerCase()
  return e ? `${base}.${e}` : base
}
