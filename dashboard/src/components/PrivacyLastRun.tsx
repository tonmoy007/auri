import { PrivacyDueCell } from '@/components/PrivacyDueCell'
import type { RetentionRun } from '@/lib/api'

interface PrivacyLastRunProps {
  run: RetentionRun | null
  minCohort: number
}

/** When the deletion job last ran and what it did, each count suppressed like the rest. */
export function PrivacyLastRun({ run, minCohort }: PrivacyLastRunProps) {
  if (run === null) return <p className="text-sm">No run has been recorded yet.</p>
  return (
    <div className="space-y-1 text-sm">
      <p>Last run {new Date(run.ran_at).toLocaleString()}:</p>
      <ul className="space-y-1">
        <li>
          Deleted: <PrivacyDueCell bucket={run.deleted} minCohort={minCohort} />
        </li>
        <li>
          Emptied to just the reply:{' '}
          <PrivacyDueCell bucket={run.emptied_to_shell} minCohort={minCohort} />
        </li>
        <li>
          Replies past their retention deleted:{' '}
          <PrivacyDueCell bucket={run.expired_replies} minCohort={minCohort} />
        </li>
        <li>
          Device records past their rate-limit window removed:{' '}
          <PrivacyDueCell bucket={run.expired_devices} minCohort={minCohort} />
        </li>
      </ul>
    </div>
  )
}
