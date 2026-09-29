import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ReplyRow } from '@/components/ReplyRow'
import type { ConfessionSummary } from '@/lib/api'

const COLUMN_COUNT = 5

interface RepliesTableProps {
  items: ConfessionSummary[]
  emptyMessage: string
  selectedId: string | null
  onSelect: (confessionId: string) => void
}

/** One page of confession summaries with their reply state. */
export function RepliesTable({ items, emptyMessage, selectedId, onSelect }: RepliesTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Received</TableHead>
          <TableHead>Category</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Reply state</TableHead>
          <TableHead className="text-right">Action</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.length === 0 ? (
          <TableRow>
            <TableCell colSpan={COLUMN_COUNT} className="text-muted-foreground">
              {emptyMessage}
            </TableCell>
          </TableRow>
        ) : (
          items.map((item) => (
            <ReplyRow
              key={item.id}
              item={item}
              selected={item.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </TableBody>
    </Table>
  )
}
