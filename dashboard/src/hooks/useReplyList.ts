import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, hrApi, type ConfessionSummary, type Requester } from '@/lib/api'

export const REPLY_FILTERS = ['needs-reply', 'replied', 'all'] as const

/** Which confessions the Replies tab lists. */
export type ReplyFilter = (typeof REPLY_FILTERS)[number]

export function isReplyFilter(value: string): value is ReplyFilter {
  return REPLY_FILTERS.some((filter) => filter === value)
}

export type ReplyListState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: ConfessionSummary[]; total: number }

type SettledState = Exclude<ReplyListState, { status: 'loading' }>

/** A finished load, tagged with the request it answers so stale results are ignored. */
interface SettledLoad {
  key: string
  state: SettledState
}

const REPLIED_QUERY: Record<ReplyFilter, boolean | undefined> = {
  'needs-reply': false,
  replied: true,
  all: undefined,
}

async function loadPage(
  request: Requester,
  filter: ReplyFilter,
  offset: number,
  pageSize: number,
): Promise<SettledState> {
  try {
    const page = await hrApi.listConfessions(request, {
      replied: REPLIED_QUERY[filter],
      limit: pageSize,
      offset,
    })
    return { status: 'ready', items: page.items, total: page.total }
  } catch (err) {
    const message = err instanceof ApiError ? err.message : 'Could not reach the backend'
    return { status: 'error', message }
  }
}

function withItemReplaced(current: SettledLoad | null, saved: ConfessionSummary): SettledLoad | null {
  if (current === null || current.state.status !== 'ready') return current
  const items = current.state.items.map((item) => (item.id === saved.id ? saved : item))
  return { ...current, state: { ...current.state, items } }
}

function withItemRemoved(current: SettledLoad | null, confessionId: string): SettledLoad | null {
  if (current === null || current.state.status !== 'ready') return current
  const items = current.state.items.filter((item) => item.id !== confessionId)
  const removed = current.state.items.length - items.length
  const total = Math.max(0, current.state.total - removed)
  return { ...current, state: { ...current.state, items, total } }
}

/**
 * Loads one page of the HR confession summaries.
 *
 * It fetches on mount, when the filter or page changes, and when `reload` is
 * called — never on a timer, because every list request is written to the
 * audit trail. Saved replies are patched into the loaded page locally
 * (`replaceItem`) instead of refetching, so a save adds no audit noise.
 */
export function useReplyList(filter: ReplyFilter, offset: number, pageSize: number) {
  const { authedRequest } = useAuth()
  const [reloadCount, setReloadCount] = useState(0)
  const [settled, setSettled] = useState<SettledLoad | null>(null)
  const requestKey = `${filter}:${offset}:${reloadCount}`

  useEffect(() => {
    let cancelled = false
    loadPage(authedRequest, filter, offset, pageSize).then((state) => {
      if (!cancelled) setSettled({ key: requestKey, state })
    })
    return () => {
      cancelled = true
    }
  }, [authedRequest, filter, offset, pageSize, requestKey])

  const reload = useCallback(() => setReloadCount((count) => count + 1), [])
  const replaceItem = useCallback(
    (saved: ConfessionSummary) => setSettled((current) => withItemReplaced(current, saved)),
    [],
  )
  const removeItem = useCallback(
    (confessionId: string) => setSettled((current) => withItemRemoved(current, confessionId)),
    [],
  )

  const state: ReplyListState =
    settled !== null && settled.key === requestKey ? settled.state : { status: 'loading' }
  return { state, reload, replaceItem, removeItem }
}
