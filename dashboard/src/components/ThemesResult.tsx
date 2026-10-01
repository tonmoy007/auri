import { ThemesDigestActions } from '@/components/ThemesDigestActions'
import { ThemesNotices } from '@/components/ThemesNotices'
import { ThemesTable } from '@/components/ThemesTable'
import type { ThemesReport } from '@/lib/api'

interface ThemesResultProps {
  report: ThemesReport
}

/** A generated report: notices, the ranked themes, and the digest downloads. */
export function ThemesResult({ report }: ThemesResultProps) {
  const analysed = report.analysed.suppressed
    ? `fewer than ${report.min_cohort}`
    : String(report.analysed.count)
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {analysed} confession(s) read, last {report.days} days compared with the {report.days} before.
      </p>
      <ThemesNotices report={report} />
      {report.themes.length > 0 ? (
        <>
          <ThemesTable themes={report.themes} minCohort={report.min_cohort} />
          <ThemesDigestActions report={report} />
        </>
      ) : (
        report.method !== 'none' && (
          <p className="text-sm text-muted-foreground">
            No theme was large enough to report without risking identifying someone.
          </p>
        )
      )}
    </div>
  )
}
