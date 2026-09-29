import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const REPLY_DISCLOSURES: string[] = [
  "They see your message under the label 'Reply from HR'. Your name and account are never shown to them. Don't sign it, and don't add links or contact details.",
  'Your account is recorded against every save in the audit trail, which admins can review.',
  "They aren't notified and you won't know whether they read it. It appears only when they open their history on the device they used.",
  'You can edit a saved reply but not withdraw it. If you edit it, they see that it was edited.',
  'Forwarded confessions are removed automatically soon after delivery (by default within a day), and the reply goes with them.',
]

/** What HR must know before writing to someone who spoke up. */
export function RepliesIntro() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Replies</CardTitle>
        <CardDescription>
          Write back on behalf of the organisation to the person behind a confession. You see
          the summary only, never the transcript. Before you write:
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {REPLY_DISCLOSURES.map((disclosure) => (
            <li key={disclosure}>{disclosure}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
