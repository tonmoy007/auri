import { RepliesTable } from '@/components/RepliesTable'
import { ReplyListError } from '@/components/ReplyListError'
import { ReplyListSkeleton } from '@/components/ReplyListSkeleton'
import type { ReplyFilter, ReplyListState } from '@/hooks/useReplyList'

const EMPTY_MESSAGES: Record<ReplyFilter, string> = {
  'needs-reply': 'Nothing needs a reply right now.',
  replied: 'No replies yet.',
  all: 'No confessions yet.',
}

interface RepliesListBodyProps {
  state: ReplyListState
  filter: ReplyFilter
  selectedId: string | null
  onSelect: (confessionId: string) => void
  onRetry: () => void
}

/** The list area: skeleton while loading, an error with Retry, or the table. */
export function RepliesListBody({
  state,
  filter,
  selectedId,
  onSelect,
  onRetry,
}: RepliesListBodyProps) {
  if (state.status === 'loading') return <ReplyListSkeleton />
  if (state.status === 'error') return <ReplyListError message={state.message} onRetry={onRetry} />

  return (
    <RepliesTable
      items={state.items}
      emptyMessage={EMPTY_MESSAGES[filter]}
      selectedId={selectedId}
      onSelect={onSelect}
    />
  )
}
