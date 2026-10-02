import { SuppressibleBarChart } from '@/components/charts/SuppressibleBarChart'
import { VolumeLineChart } from '@/components/charts/VolumeLineChart'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { Bucket, WeekInsights } from '@/lib/api'

const STATUS_TITLES: Record<string, string> = {
  pending: 'Kept private',
  forwarded: 'Forwarded',
  flagged: 'Flagged',
}

/** One number, or a plain statement that it is withheld to protect people. */
function BucketValue({ bucket, minCohort }: { bucket: Bucket; minCohort: number }) {
  if (bucket.suppressed) {
    return (
      <span className="text-sm text-muted-foreground">
        Hidden (fewer than {minCohort}, or it would reveal one that is)
      </span>
    )
  }
  return <span className="text-2xl font-semibold">{bucket.count}</span>
}

function KpiTile({ title, bucket, minCohort }: { title: string; bucket: Bucket; minCohort: number }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{title}</CardDescription>
      </CardHeader>
      <CardContent>
        <BucketValue bucket={bucket} minCohort={minCohort} />
      </CardContent>
    </Card>
  )
}

function ChartCard({
  title,
  description,
  buckets,
  minCohort,
}: {
  title: string
  description: string
  buckets: Bucket[]
  minCohort: number
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <SuppressibleBarChart buckets={buckets} minCohort={minCohort} seriesLabel={title} />
      </CardContent>
    </Card>
  )
}

/** Shown for a week whose figures are not final yet. */
function NotReady() {
  return (
    <Alert>
      <AlertTitle>Not available yet</AlertTitle>
      <AlertDescription>
        A week appears once it has ended and every confession from it has been
        removed under the retention rule, normally within a day or two. From then
        on its figures never change, so no two views of it can be compared to
        single anyone out.
      </AlertDescription>
    </Alert>
  )
}

/** One fixed week of Insights; every figure arrives already suppressed. */
export function InsightsWeek({ week, minCohort }: { week: WeekInsights; minCohort: number }) {
  if (!week.frozen || week.total === null) return <NotReady />
  const delivered = week.delivery.find((b) => b.label === 'delivered')
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <KpiTile title="Total" bucket={week.total} minCohort={minCohort} />
        {week.by_status.map((bucket) => (
          <KpiTile
            key={bucket.label}
            title={STATUS_TITLES[bucket.label] ?? bucket.label}
            bucket={bucket}
            minCohort={minCohort}
          />
        ))}
        {delivered && <KpiTile title="Delivered" bucket={delivered} minCohort={minCohort} />}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Volume</CardTitle>
          <CardDescription>Confessions made each day of the week.</CardDescription>
        </CardHeader>
        <CardContent>
          <VolumeLineChart buckets={week.by_day} minCohort={minCohort} />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Categories"
          description="What people are talking about."
          buckets={week.by_category}
          minCohort={minCohort}
        />
        <ChartCard
          title="Sentiment"
          description="Overall tone this week."
          buckets={week.by_sentiment}
          minCohort={minCohort}
        />
      </div>

      <ChartCard
        title="Time to delivery"
        description="How long delivered confessions took to reach their department."
        buckets={week.delivery_time}
        minCohort={minCohort}
      />

      <Card>
        <CardHeader>
          <CardTitle>Departments</CardTitle>
          <CardDescription>
            Where confessions were sent. Small teams are exactly where a count
            identifies someone, so they are withheld.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Department</TableHead>
                <TableHead>Confessions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {week.by_department.map((bucket) => (
                <TableRow key={bucket.label}>
                  <TableCell>{bucket.label}</TableCell>
                  <TableCell>
                    <BucketValue bucket={bucket} minCohort={minCohort} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
