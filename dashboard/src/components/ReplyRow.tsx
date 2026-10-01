import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { TableCell, TableRow } from '@/components/ui/table'
import { ReplyStateBadge } from '@/components/ReplyStateBadge'
import type { ConfessionSummary } from '@/lib/api'

interface ReplyRowProps {
  item: ConfessionSummary
  selected: boolean
  onSelect: (confessionId: string) => void
}

/** One confession summary row with a keyboard-accessible Open button. */
export function ReplyRow({ item, selected, onSelect }: ReplyRowProps) {
  const received = new Date(item.created_at).toLocaleString()

  return (
    <TableRow data-state={selected ? 'selected' : undefined}>
      <TableCell className="whitespace-nowrap font-mono text-xs">{received}</TableCell>
      <TableCell>{item.category ?? '—'}</TableCell>
      <TableCell>
        {item.status}
        {item.severity === 'crisis' && (
          <Badge variant="destructive" className="ml-2">
            crisis
          </Badge>
        )}
      </TableCell>
      <TableCell>
        <ReplyStateBadge item={item} />
      </TableCell>
      <TableCell className="text-right">
        <Button
          variant={selected ? 'secondary' : 'outline'}
          size="sm"
          aria-current={selected ? 'true' : undefined}
          aria-label={`Open confession received ${received}`}
          onClick={() => onSelect(item.id)}
        >
          Open
        </Button>
      </TableCell>
    </TableRow>
  )
}
