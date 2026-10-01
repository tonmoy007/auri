import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { SuppressibleBarChart } from '@/components/charts/SuppressibleBarChart'
import { VolumeLineChart } from '@/components/charts/VolumeLineChart'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, hrApi, type Bucket, type Insights } from '@/lib/api'

const DEFAULT_RANGE_DAYS = 30

function isoDate(daysAgo: number): string {
  const moment = new Date()
  moment.setDate(moment.getDate() - daysAgo)
  return moment.toISOString().slice(0, 10)
}

/** Renders a single number, or says plainly why it is being withheld. */
function BucketValue({ bucket, minCohort }: { bucket: Bucket; minCohort: number }) {
  if (bucket.suppressed) {
    return (
      <span className="text-sm text-muted-foreground">
        Hidden (fewer than {minCohort})
      </span>
    )
  }
  return <span className="text-2xl font-semibold">{bucket.count}</span>
}

function KpiTile({
  title,
  bucket,
  minCohort,
}: {
  title: string
  bucket: Bucket
  minCohort: number
}) {
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

/** Aggregate reporting for HR, with every small cohort visibly protected. */
export function InsightsPanel() {
  const { authedRequest } = useAuth()
  const [insights, setInsights] = useState<Insights | null>(null)
  const [since, setSince] = useState(isoDate(DEFAULT_RANGE_DAYS))
  const [until, setUntil] = useState(isoDate(0))

  const load = useCallback(async () => {
    try {
      setInsights(
        await hrApi.getInsights(authedRequest, {
          since: `${since}T00:00:00Z`,
          until: `${until}T23:59:59Z`,
        }),
      )
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not load insights')
    }
  }, [authedRequest, since, until])

  useEffect(() => {
    load()
  }, [load])

  if (!insights) return <Skeleton className="h-96 w-full" />

  const { min_cohort: minCohort } = insights

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-card p-4">
        <div className="grid gap-1.5">
          <Label htmlFor="insights-since">From</Label>
          <Input
            id="insights-since"
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
            className="w-44"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="insights-until">To</Label>
          <Input
            id="insights-until"
            type="date"
            value={until}
            onChange={(e) => setUntil(e.target.value)}
            className="w-44"
          />
        </div>
        <Button variant="outline" onClick={load}>
          Refresh
        </Button>
        <p className="mb-2 text-xs text-muted-foreground">
          Cohorts smaller than {minCohort} are never shown.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiTile title="Total" bucket={insights.total} minCohort={minCohort} />
        <KpiTile title="Forwarded" bucket={insights.forwarded} minCohort={minCohort} />
        <KpiTile title="Kept private" bucket={insights.blind} minCohort={minCohort} />
        <KpiTile title="Flagged" bucket={insights.flagged} minCohort={minCohort} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Flagged rate</CardDescription>
          </CardHeader>
          <CardContent>
            {insights.flagged_rate === null ? (
              <span className="text-sm text-muted-foreground">
                Hidden to protect anonymity
              </span>
            ) : (
              <span className="text-2xl font-semibold">
                {(insights.flagged_rate * 100).toFixed(1)}%
              </span>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Median time to delivery</CardDescription>
          </CardHeader>
          <CardContent>
            {insights.median_hours_to_delivery === null ? (
              <span className="text-sm text-muted-foreground">
                Hidden to protect anonymity
              </span>
            ) : (
              <span className="text-2xl font-semibold">
                {insights.median_hours_to_delivery} h
              </span>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Volume</CardTitle>
          <CardDescription>Confessions submitted per day.</CardDescription>
        </CardHeader>
        <CardContent>
          <VolumeLineChart buckets={insights.volume_by_day} minCohort={minCohort} />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Categories</CardTitle>
            <CardDescription>What people are talking about.</CardDescription>
          </CardHeader>
          <CardContent>
            <SuppressibleBarChart
              buckets={insights.by_category}
              minCohort={minCohort}
              seriesLabel="Confessions by category"
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Sentiment</CardTitle>
            <CardDescription>Overall tone across the range.</CardDescription>
          </CardHeader>
          <CardContent>
            <SuppressibleBarChart
              buckets={insights.by_sentiment}
              minCohort={minCohort}
              seriesLabel="Confessions by sentiment"
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Departments</CardTitle>
          <CardDescription>
            Where forwarded confessions went. Small teams are exactly where a count
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
              {insights.by_department.map((bucket) => (
                <TableRow key={bucket.label}>
                  <TableCell>{bucket.label}</TableCell>
                  <TableCell>
                    {bucket.suppressed ? (
                      <span className="text-muted-foreground">
                        Hidden to protect anonymity (fewer than {minCohort})
                      </span>
                    ) : (
                      bucket.count
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sentiment trend</CardTitle>
          <CardDescription>Weekly split; each bucket is protected on its own.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Week</TableHead>
                <TableHead>Negative</TableHead>
                <TableHead>Neutral</TableHead>
                <TableHead>Positive</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {insights.sentiment_trend.map((point) => (
                <TableRow key={point.label}>
                  <TableCell className="font-mono text-xs">{point.label}</TableCell>
                  {point.buckets.map((bucket) => (
                    <TableCell key={bucket.label}>
                      {bucket.suppressed ? (
                        <span className="text-muted-foreground">hidden</span>
                      ) : (
                        bucket.count
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
