import { PrivacyDueCell } from '@/components/PrivacyDueCell'
import { PrivacyLastRun } from '@/components/PrivacyLastRun'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { RetentionOverview } from '@/lib/api'

interface PrivacyRetentionCardProps {
  retention: RetentionOverview
  minCohort: number
}

/** The retention promise, evidence the deletion job is keeping it, and what is due next. */
export function PrivacyRetentionCard({ retention, minCohort }: PrivacyRetentionCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Retention</CardTitle>
        <CardDescription>
          A forwarded or withdrawn confession is removed at the first run after it has been
          unchanged for {retention.retention_hours} hours; the job is expected to run at least
          every {retention.expected_run_hours} hours. When HR replied, the reply is kept for{' '}
          {retention.reply_retention_days} days. Confessions that are never forwarded, or are
          held for review, are kept until someone acts on them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {retention.overdue && (
          <Alert variant="destructive">
            <AlertTitle>The deletion job is overdue</AlertTitle>
            <AlertDescription>
              It has not run within the last {retention.expected_run_hours} hours, so confessions
              may be outliving the promise above. Check the scheduled job.
            </AlertDescription>
          </Alert>
        )}
        <PrivacyLastRun run={retention.last_run} minCohort={minCohort} />
        <p className="text-sm text-muted-foreground">Waiting for the next run:</p>
        <ul className="space-y-1 text-sm">
          <li>
            To delete: <PrivacyDueCell bucket={retention.due_to_delete} minCohort={minCohort} />
          </li>
          <li>
            To empty to just the reply:{' '}
            <PrivacyDueCell bucket={retention.due_to_empty} minCohort={minCohort} />
          </li>
          <li>
            Replies past their retention:{' '}
            <PrivacyDueCell bucket={retention.due_to_expire} minCohort={minCohort} />
          </li>
        </ul>
      </CardContent>
    </Card>
  )
}
