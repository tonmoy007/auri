import type { ThemeEntry } from '@/lib/api'

interface ThemeSentimentCellProps {
  theme: ThemeEntry
  minCohort: number
}

/** How negative sentiment moved for a theme, or why that is not shown. */
export function ThemeSentimentCell({ theme, minCohort }: ThemeSentimentCellProps) {
  if (theme.sentiment_status === 'no_previous_period') {
    return <span className="text-sm text-muted-foreground">New this period</span>
  }
  if (theme.sentiment_status === 'suppressed' || theme.sentiment_change === null) {
    return (
      <span className="text-sm text-muted-foreground">
        Hidden (fewer than {minCohort} with a sentiment)
      </span>
    )
  }
  const points = Math.round(theme.sentiment_change * 100)
  if (points === 0) return <span className="text-sm text-muted-foreground">No change</span>
  if (points < 0) {
    return <span className="text-sm text-muted-foreground">{Math.abs(points)} pts less negative</span>
  }
  return <span className="text-sm text-destructive">{points} pts more negative</span>
}
