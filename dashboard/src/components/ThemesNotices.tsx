import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import type { ThemesReport } from '@/lib/api'

interface ThemesNoticesProps {
  report: ThemesReport
}

/** Everything HR must know to read the themes correctly: fallback, partial view, withheld themes. */
export function ThemesNotices({ report }: ThemesNoticesProps) {
  return (
    <div className="space-y-3">
      {report.notice && (
        <Alert>
          <AlertTitle>{report.method === 'none' ? 'Not enough data' : 'Grouped by category'}</AlertTitle>
          <AlertDescription>{report.notice}</AlertDescription>
        </Alert>
      )}
      {report.truncated && (
        <Alert>
          <AlertTitle>Partial view</AlertTitle>
          <AlertDescription>
            This period holds more confessions than the report reads, so it uses the most recent
            ones.
          </AlertDescription>
        </Alert>
      )}
      {report.hidden_themes > 0 && (
        <p className="text-sm text-muted-foreground">
          {report.hidden_themes} further theme(s) hidden to protect anonymity (fewer than{' '}
          {report.min_cohort} confessions).
        </p>
      )}
    </div>
  )
}
