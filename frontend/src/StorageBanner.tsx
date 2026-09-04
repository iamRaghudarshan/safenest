/**
 * The line across the top when the app is running on this computer's own copy
 * because the drive the records normally live on cannot be read.
 *
 * Not a modal and not a gate. The launcher has already dealt with it: there were
 * records here, so the app opened on them and the person can work. What is left
 * is one obligation -- they must not find out LATER that today's entries went
 * somewhere other than they assumed. So this says where changes are going, and
 * it does not dismiss.
 */
import { useEffect, useState } from 'react'
import { api, type StorageBlock } from './api'

export function StorageBanner() {
  const [info, setInfo] = useState<StorageBlock | null>(null)

  useEffect(() => {
    // Asked for directly rather than waiting for a failing request to reveal it:
    // in fallback mode nothing fails, which is the point, so nothing would ever
    // surface it on its own.
    api<{ ok: boolean } & StorageBlock>('/api/storage/problem')
      .then((r) => setInfo(r.ok ? null : r))
      .catch(() => setInfo(null))
  }, [])

  if (!info || info.mode !== 'fallback') return null
  const where = info.volume || info.folder || 'another disk'

  return (
    <div className="banner warn" role="status">
      <strong>Your records drive isn't readable.</strong>{' '}
      SafeNest is using the copy on this computer, so you can carry on.{' '}
      <strong>Nothing has been deleted</strong> — everything is still on {where}.
      Anything you change now stays on this computer until that drive is back.
    </div>
  )
}
