import { RepliesFilterBar } from '@/components/RepliesFilterBar'
import { RepliesIntro } from '@/components/RepliesIntro'
import { RepliesListBody } from '@/components/RepliesListBody'
import { RepliesPager } from '@/components/RepliesPager'
import { ReplyEditor } from '@/components/ReplyEditor'
import { REPLY_PAGE_SIZE, useReplyDesk } from '@/hooks/useReplyDesk'

/**
 * HR's reply desk: pick a confession from the summary list and write back.
 *
 * Only the summary tier is ever requested. The list is fetched on mount, on
 * filter or page change, and on Refresh or Retry; a save patches the saved row
 * in place rather than refetching, since every list request is audited.
 */
export function RepliesPanel() {
  const desk = useReplyDesk()
  const { state, selected } = desk

  return (
    <div className="space-y-4">
      <RepliesIntro />
      <RepliesFilterBar
        filter={desk.filter}
        total={state.status === 'ready' ? state.total : null}
        onFilterChange={desk.changeFilter}
        onRefresh={desk.reload}
      />
      <RepliesListBody
        state={state}
        filter={desk.filter}
        selectedId={selected?.id ?? null}
        onSelect={desk.select}
        onRetry={desk.reload}
      />
      {state.status === 'ready' && (
        <RepliesPager
          offset={desk.offset}
          total={state.total}
          pageSize={REPLY_PAGE_SIZE}
          onOffsetChange={desk.changeOffset}
        />
      )}
      {selected !== null && (
        <ReplyEditor
          key={selected.id}
          item={selected}
          saving={desk.saving}
          onSave={(reply) => desk.save(selected.id, reply)}
        />
      )}
    </div>
  )
}
