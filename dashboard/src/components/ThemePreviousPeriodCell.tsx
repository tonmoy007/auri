import type { Bucket } from '@/lib/api'

interface ThemePreviousPeriodCellProps {
  bucket: Bucket
  minCohort: number
}

/** The previous period's count for a theme, or why it is withheld. */
export function ThemePreviousPeriodCell({ bucket, minCohort }: ThemePreviousPeriodCellProps) {
  if (bucket.suppressed) {
    return (
      <span className="text-sm text-muted-foreground">Hidden (fewer than {minCohort})</span>
    )
  }
  return <span>{bucket.count}</span>
}
