import { useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { ExistingReply } from '@/components/ExistingReply'
import { ReplyDraftField } from '@/components/ReplyDraftField'
import { ReplyNotices } from '@/components/ReplyNotices'
import { HR_REPLY_MAX_LENGTH, type ConfessionSummary } from '@/lib/api'

/** Whether the draft differs from what is saved and is fit to send. */
function isSavable(draft: string, saved: string | null): boolean {
  const trimmed = draft.trim()
  if (trimmed === '') return false
  if (draft.length > HR_REPLY_MAX_LENGTH) return false
  return trimmed !== (saved ?? '')
}

interface ReplyEditorProps {
  item: ConfessionSummary
  saving: boolean
  onSave: (reply: string) => void
}

/**
 * Edits the organisation's reply to one confession.
 *
 * Shows the AI summary only and never asks for the transcript: replying does
 * not require reading the raw words. Render with `key={item.id}` so the draft
 * starts fresh for each confession.
 */
export function ReplyEditor({ item, saving, onSave }: ReplyEditorProps) {
  const [draft, setDraft] = useState(item.hr_reply ?? '')
  const held = item.status === 'flagged'
  const canSave = !saving && !held && isSavable(draft, item.hr_reply)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{item.category ?? 'uncategorised'}</CardTitle>
        <CardDescription>
          Received {new Date(item.created_at).toLocaleString()}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ReplyNotices held={held} crisis={item.severity === 'crisis'} />
        <p className="rounded-md bg-muted p-3 text-sm">
          {item.ai_summary ?? 'No summary available'}
        </p>
        <ExistingReply item={item} />
        <ReplyDraftField value={draft} disabled={held} onChange={setDraft} />
        <Button disabled={!canSave} onClick={() => onSave(draft)}>
          {saving ? 'Saving…' : 'Save reply'}
        </Button>
      </CardContent>
    </Card>
  )
}
