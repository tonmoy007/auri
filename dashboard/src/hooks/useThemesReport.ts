import { useCallback, useRef, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, hrApi, type ThemesReport } from '@/lib/api'

export type ThemesState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; report: ThemesReport }

/**
 * Generates the themes report on demand.
 *
 * Deliberately not loaded on tab open: each run asks the local model, which
 * can take a minute, so it happens when HR asks for it. A run that finishes
 * after a newer one started is ignored rather than overwriting it.
 */
export function useThemesReport() {
  const { authedRequest } = useAuth()
  const [state, setState] = useState<ThemesState>({ status: 'idle' })
  const latestRun = useRef(0)

  const generate = useCallback(
    async (days: number) => {
      const run = ++latestRun.current
      setState({ status: 'loading' })
      try {
        const report = await hrApi.getThemes(authedRequest, days)
        if (run === latestRun.current) setState({ status: 'ready', report })
      } catch (err) {
        if (run !== latestRun.current) return
        const message = err instanceof ApiError ? err.message : 'Could not reach the backend'
        setState({ status: 'error', message })
      }
    },
    [authedRequest],
  )

  return { state, generate }
}
