// Generic list-resource hook: load once, plus create/update/remove that refetch.
import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage } from './api'

export function useResource<T>(path: string) {
  const [items, setItems] = useState<T[]>([])
  const [extra, setExtra] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // silent=true keeps the current list on screen (used by pull-to-refresh) instead
  // of flashing the full-screen spinner.
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const data = await api<{ items: T[] } & Record<string, unknown>>(path)
      setItems(data.items || [])
      const { items: _drop, ...rest } = data
      void _drop
      setExtra(rest)
    } catch (e) {
      setError(errorMessage(e, 'Could not load this list'))
    } finally {
      if (!silent) setLoading(false)
    }
  }, [path])

  useEffect(() => { load() }, [load])

  const refresh = useCallback(() => load(true), [load])

  const create = useCallback(async (body: unknown) => {
    await api(path, { method: 'POST', body }); await load()
  }, [path, load])

  const update = useCallback(async (id: number, body: unknown) => {
    await api(`${path}/${id}`, { method: 'PUT', body }); await load()
  }, [path, load])

  const remove = useCallback(async (id: number) => {
    await api(`${path}/${id}`, { method: 'DELETE' }); await load()
  }, [path, load])

  return { items, extra, loading, error, reload: load, refresh, create, update, remove }
}
