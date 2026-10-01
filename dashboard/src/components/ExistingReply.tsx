import type { ConfessionSummary } from '@/lib/api'

interface ExistingReplyProps {
  item: ConfessionSummary
}

/** The reply already saved, rendered as plain text — never as markup or links. */
export function ExistingReply({ item }: ExistingReplyProps) {
  if (item.hr_reply === null) return null

  return (
    <div className="space-y-1">
      <p className="text-sm font-medium text-card-foreground">Current reply</p>
      <p className="rounded-md bg-muted p-3 text-sm break-words whitespace-pre-wrap">
        {item.hr_reply}
      </p>
      <p className="text-xs text-muted-foreground">
        {item.hr_replied_at !== null && `Replied ${new Date(item.hr_replied_at).toLocaleString()}`}
        {item.hr_reply_edited_at !== null &&
          ` · Edited ${new Date(item.hr_reply_edited_at).toLocaleString()}`}
      </p>
    </div>
  )
}
