import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { isReplyFilter, type ReplyFilter } from '@/hooks/useReplyList'

const FILTER_OPTIONS: { value: ReplyFilter; label: string }[] = [
  { value: 'needs-reply', label: 'Needs a reply' },
  { value: 'replied', label: 'Replied' },
  { value: 'all', label: 'All confessions' },
]

interface RepliesFilterBarProps {
  filter: ReplyFilter
  /** `null` while the count is not known (loading or failed). */
  total: number | null
  onFilterChange: (next: ReplyFilter) => void
  onRefresh: () => void
}

/** Filter select, Refresh button and the matching-confession count. */
export function RepliesFilterBar({
  filter,
  total,
  onFilterChange,
  onRefresh,
}: RepliesFilterBarProps) {
  const handleValueChange = (value: string) => {
    if (isReplyFilter(value)) onFilterChange(value)
  }

  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="grid gap-1.5">
        <Label htmlFor="replies-filter">Show</Label>
        <Select value={filter} onValueChange={handleValueChange}>
          <SelectTrigger id="replies-filter" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FILTER_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button variant="outline" onClick={onRefresh}>
        Refresh
      </Button>
      {total !== null && (
        <span className="mb-2 text-sm text-muted-foreground">{total} confession(s)</span>
      )}
    </div>
  )
}
