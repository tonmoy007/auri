import { useState } from 'react'
import { useReplyList, type ReplyFilter, type ReplyListState } from '@/hooks/useReplyList'
import { useReplySave } from '@/hooks/useReplySave'
import type { ConfessionSummary } from '@/lib/api'

export const REPLY_PAGE_SIZE = 25
const DEFAULT_FILTER: ReplyFilter = 'needs-reply'

function findSelected(state: ReplyListState, selectedId: string | null): ConfessionSummary | null {
  if (state.status !== 'ready' || selectedId === null) return null
  return state.items.find((item) => item.id === selectedId) ?? null
}

/**
 * The state behind the Replies tab: filter, page, selection, the loaded list
 * and the save action.
 *
 * Changing the filter resets to the first page and clears the selection;
 * changing the page clears it too. A save patches the saved row in place, and
 * a 404 on save (the confession was deleted) drops the row and the selection.
 */
export function useReplyDesk() {
  const [filter, setFilter] = useState<ReplyFilter>(DEFAULT_FILTER)
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { state, reload, replaceItem, removeItem } = useReplyList(filter, offset, REPLY_PAGE_SIZE)
  const { saving, save } = useReplySave(replaceItem, (confessionId) => {
    removeItem(confessionId)
    setSelectedId(null)
  })

  const changeFilter = (next: ReplyFilter) => {
    setFilter(next)
    setOffset(0)
    setSelectedId(null)
  }
  const changeOffset = (next: number) => {
    setOffset(next)
    setSelectedId(null)
  }

  return {
    filter,
    offset,
    state,
    selected: findSelected(state, selectedId),
    saving,
    reload,
    save,
    select: setSelectedId,
    changeFilter,
    changeOffset,
  }
}
