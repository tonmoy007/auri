import { Button } from '@/components/ui/button'

interface RepliesPagerProps {
  offset: number
  total: number
  pageSize: number
  onOffsetChange: (next: number) => void
}

/** Previous / Next controls over an offset-paged list. */
export function RepliesPager({ offset, total, pageSize, onOffsetChange }: RepliesPagerProps) {
  const lastPageOffset = Math.max(0, Math.floor((total - 1) / pageSize) * pageSize)

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        disabled={offset === 0}
        onClick={() => onOffsetChange(Math.max(0, offset - pageSize))}
      >
        Previous
      </Button>
      <Button
        variant="outline"
        disabled={offset >= lastPageOffset}
        onClick={() => onOffsetChange(offset + pageSize)}
      >
        Next
      </Button>
    </div>
  )
}
