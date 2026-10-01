import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { errorMessage, useAdminResource } from '@/hooks/useAdminResource'
import { useAuth } from '@/hooks/useAuth'
import {
  ApiError,
  priestAdminApi,
  type PriestReindexStatus,
} from '@/lib/api'

const POLL_INTERVAL_MS = 2000
const ALREADY_RUNNING = 409

const EMPTY_RUNNING: PriestReindexStatus = {
  state: 'running',
  started_at: null,
  finished_at: null,
  pid: null,
  notes_total: 0,
  notes_indexed: 0,
  chunks: 0,
  exclusions: {},
  error_code: null,
}

/**
 * The index, the last build's status and the actions on them.
 *
 * While a build runs the status is polled every two seconds, like the APK build;
 * when it stops running the index is reloaded so the new version shows up.
 */
export function usePriestIndex() {
  const { authedRequest } = useAuth()
  const { state: index, reload } = useAdminResource(priestAdminApi.getIndex)
  const [reindex, setReindex] = useState<PriestReindexStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [activating, setActivating] = useState<string | null>(null)

  const refreshStatus = useCallback(async () => {
    const next = await priestAdminApi.getReindexStatus(authedRequest)
    setReindex(next)
    return next
  }, [authedRequest])

  useEffect(() => {
    let cancelled = false
    priestAdminApi.getReindexStatus(authedRequest).then(
      (next) => {
        if (!cancelled) setReindex(next)
      },
      () => {
        // No readable status yet; the card shows the index on its own.
      },
    )
    return () => {
      cancelled = true
    }
  }, [authedRequest])

  const running = reindex?.state === 'running'
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => {
      refreshStatus()
        .then((next) => {
          if (next.state !== 'running') reload()
        })
        .catch(() => {
          // A missed poll is retried on the next tick; no toast spam.
        })
    }, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [running, refreshStatus, reload])

  const startReindex = useCallback(async () => {
    setStarting(true)
    try {
      const started = await priestAdminApi.startReindex(authedRequest)
      setReindex({ ...EMPTY_RUNNING, started_at: started.started_at })
    } catch (err) {
      if (err instanceof ApiError && err.status === ALREADY_RUNNING) {
        await refreshStatus().catch(() => undefined)
      } else {
        toast.error(errorMessage(err, 'Could not start the reindex'))
      }
    } finally {
      setStarting(false)
    }
  }, [authedRequest, refreshStatus])

  const activate = useCallback(
    async (version: string) => {
      setActivating(version)
      try {
        await priestAdminApi.activate(authedRequest, version)
        toast.success(`Index ${version} is now active`)
        reload()
      } catch (err) {
        toast.error(errorMessage(err, 'Could not activate that version'))
      } finally {
        setActivating(null)
      }
    },
    [authedRequest, reload],
  )

  return { index, reindex, starting, activating, startReindex, activate, reload }
}
