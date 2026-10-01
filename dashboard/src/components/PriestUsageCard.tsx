import { ResourceView } from '@/components/ResourceView'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAdminResource } from '@/hooks/useAdminResource'
import { priestAdminApi, type PriestUsage } from '@/lib/api'
import { formatCount, formatSeconds, humanizeKey } from '@/lib/priestFormat'

function OutcomeTable({ usage }: { usage: PriestUsage }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Outcome</TableHead>
          <TableHead className="text-right">Questions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {usage.outcomes.map((outcome) => (
          <TableRow key={outcome.kind}>
            <TableCell>{humanizeKey(outcome.kind)}</TableCell>
            <TableCell className="text-right">
              {formatCount(outcome.count, outcome.suppressed, usage.min_cohort)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function LatencyTable({ usage }: { usage: PriestUsage }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Stage</TableHead>
          <TableHead className="text-right">p50</TableHead>
          <TableHead className="text-right">p95</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {usage.latency.map((row) => (
          <TableRow key={row.stage}>
            <TableCell>{humanizeKey(row.stage)}</TableCell>
            <TableCell className="text-right">{formatSeconds(row.p50)}</TableCell>
            <TableCell className="text-right">{formatSeconds(row.p95)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Counts by outcome and latency since the process started. Never any text. */
export function PriestUsageCard() {
  const { state, reload } = useAdminResource(priestAdminApi.getUsage)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Usage</CardTitle>
        <CardDescription>
          Counts and timings since the server process started. Small counts are withheld, and no
          question or answer is ever shown here.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ResourceView state={state} label="usage" onRetry={reload}>
          {(usage) =>
            usage.outcomes.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No questions since the server process started.
              </p>
            ) : (
              <>
                <OutcomeTable usage={usage} />
                <LatencyTable usage={usage} />
              </>
            )
          }
        </ResourceView>
      </CardContent>
    </Card>
  )
}
