import { Badge } from '@/components/ui/badge'
import type { ConfessionSummary } from '@/lib/api'

interface ReplyStateBadgeProps {
  item: ConfessionSummary
}

/** Whether a confession is held for moderation, already replied to, or waiting for a reply. */
export function ReplyStateBadge({ item }: ReplyStateBadgeProps) {
  if (item.status === 'flagged') return <Badge variant="secondary">Held</Badge>
  if (item.hr_replied_at !== null) return <Badge>Replied</Badge>
  return <Badge variant="outline">Needs reply</Badge>
}
