import { ResourceView } from '@/components/ResourceView'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAdminResource } from '@/hooks/useAdminResource'
import { priestAdminApi } from '@/lib/api'
import { reportRows, reportVerdict } from '@/lib/priestFormat'

function ReportBody({ summary }: { summary: Record<string, unknown> }) {
  const verdict = reportVerdict(summary)
  return (
    <div className="space-y-3">
      {verdict && (
        <Badge variant={verdict === 'pass' ? 'default' : 'destructive'}>
          {verdict === 'pass' ? 'Pass' : 'Fail'}
        </Badge>
      )}
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
        {reportRows(summary).map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** The latest evaluation run's figures, or a note that none has been written yet. */
export function PriestEvalCard() {
  const { state, reload } = useAdminResource(priestAdminApi.getReport)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Evaluation</CardTitle>
        <CardDescription>
          The latest retrieval and model benchmark, written by the evaluation scripts.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ResourceView state={state} label="the evaluation report" onRetry={reload}>
          {(report) =>
            report.available && report.summary !== null ? (
              <ReportBody summary={report.summary} />
            ) : (
              <p className="text-sm text-muted-foreground">
                No evaluation report yet. Run the evaluation scripts to produce one.
              </p>
            )
          }
        </ResourceView>
      </CardContent>
    </Card>
  )
}
