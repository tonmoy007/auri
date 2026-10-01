import { PrivacyFactsCard } from '@/components/PrivacyFactsCard'
import { PrivacyRetentionCard } from '@/components/PrivacyRetentionCard'
import { PrivacyStaffCard } from '@/components/PrivacyStaffCard'
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { usePrivacyOverview } from '@/hooks/usePrivacyOverview'

/**
 * Privacy and retention, in words an employee can be shown.
 *
 * Built from the live configuration on the server, so it cannot claim more
 * than the system does; the things it does not protect against are listed as
 * plainly as the things it does.
 */
export function PrivacyPanel() {
  const { state, reload } = usePrivacyOverview()

  if (state.status === 'loading') {
    return (
      <div className="space-y-4" role="status" aria-busy="true" aria-label="Loading the privacy overview">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  }
  if (state.status === 'error') {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load the privacy overview</AlertTitle>
        <AlertDescription>{state.message}</AlertDescription>
        <AlertAction>
          <Button variant="outline" size="sm" onClick={reload}>
            Retry
          </Button>
        </AlertAction>
      </Alert>
    )
  }

  const { overview } = state
  return (
    <div className="space-y-6">
      <PrivacyFactsCard
        title="What is guaranteed"
        description="How anonymity works, as it is configured right now."
        facts={overview.guarantees}
      />
      <PrivacyFactsCard
        title="What is not protected"
        description="The limits, stated as plainly as the guarantees."
        facts={overview.limits}
      />
      <PrivacyRetentionCard retention={overview.retention} minCohort={overview.min_cohort} />
      <PrivacyStaffCard staff={overview.staff} />
    </div>
  )
}
