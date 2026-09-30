import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, type Requester } from '@/lib/api'

export type ResourceState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T }

/** The text a failed request shows the operator. */
export function errorMessage(err: unknown, fallback = 'Could not reach the backend'): string {
  return err instanceof ApiError ? err.message : fallback
}

/**
 * Loads one admin resource on mount and whenever `reload` is called.
 *
 * `fetcher` must be a stable function (a member of an api object). The previous
 * result stays on screen while a reload is in flight, so a card does not flash
 * back to a skeleton each time its data is refreshed.
 */
export function useAdminResource<T>(fetcher: (request: Requester) => Promise<T>) {
  const { authedRequest } = useAuth()
  const [reloadCount, setReloadCount] = useState(0)
  const [state, setState] = useState<ResourceState<T>>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    fetcher(authedRequest).then(
      (data) => {
        if (!cancelled) setState({ status: 'ready', data })
      },
      (err: unknown) => {
        if (!cancelled) setState({ status: 'error', message: errorMessage(err) })
      },
    )
    return () => {
      cancelled = true
    }
  }, [fetcher, authedRequest, reloadCount])

  const reload = useCallback(() => setReloadCount((count) => count + 1), [])
  return { state, reload }
}
