import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, privacyApi, type PrivacyOverview, type Requester } from '@/lib/api'

export type PrivacyOverviewState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; overview: PrivacyOverview }

type SettledState = Exclude<PrivacyOverviewState, { status: 'loading' }>

/** A finished load, tagged with the request it answers so stale results are ignored. */
interface SettledLoad {
  key: number
  state: SettledState
}

async function load(request: Requester): Promise<SettledState> {
  try {
    return { status: 'ready', overview: await privacyApi.getOverview(request) }
  } catch (err) {
    const message = err instanceof ApiError ? err.message : 'Could not reach the backend'
    return { status: 'error', message }
  }
}

/** Loads the Privacy overview on mount and whenever `reload` is called. */
export function usePrivacyOverview() {
  const { authedRequest } = useAuth()
  const [reloadCount, setReloadCount] = useState(0)
  const [settled, setSettled] = useState<SettledLoad | null>(null)

  useEffect(() => {
    let cancelled = false
    load(authedRequest).then((state) => {
      if (!cancelled) setSettled({ key: reloadCount, state })
    })
    return () => {
      cancelled = true
    }
  }, [authedRequest, reloadCount])

  const reload = useCallback(() => setReloadCount((count) => count + 1), [])
  const state: PrivacyOverviewState =
    settled !== null && settled.key === reloadCount ? settled.state : { status: 'loading' }
  return { state, reload }
}
