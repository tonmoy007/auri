import type { Bucket } from '@/lib/api'

interface PrivacyDueCellProps {
  bucket: Bucket
  minCohort: number
}

/** A "due at the next run" count, or why it is withheld. */
export function PrivacyDueCell({ bucket, minCohort }: PrivacyDueCellProps) {
  if (bucket.suppressed) {
    return (
      <span className="text-sm text-muted-foreground">Hidden (fewer than {minCohort})</span>
    )
  }
  return <span>{bucket.count}</span>
}
