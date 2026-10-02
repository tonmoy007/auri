import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { InsightsWeek } from '@/components/InsightsWeek'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, hrApi, type Insights } from '@/lib/api'
import { defaultInsightsMonth, latestMonth, weekRangeLabel } from '@/lib/insightsMonth'

/**
 * Aggregate reporting for HR by fixed week (plan 14.1).
 *
 * HR picks a calendar month and reads its four fixed weeks. Free date ranges are
 * gone on purpose: two overlapping ranges could be subtracted to reveal a small
 * group that each range alone hid.
 */
export function InsightsPanel() {
  const { authedRequest } = useAuth()
  const [insights, setInsights] = useState<Insights | null>(null)
  const [month, setMonth] = useState(() => defaultInsightsMonth(new Date()))

  const load = useCallback(async () => {
    try {
      setInsights(await hrApi.getInsights(authedRequest, month))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not load insights')
    }
  }, [authedRequest, month])

  useEffect(() => {
    load()
  }, [load])

  if (!insights) return <Skeleton className="h-96 w-full" />

  const firstReady = insights.weeks.find((w) => w.frozen)?.label ?? insights.weeks[0]?.label

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-card p-4">
        <div className="grid gap-1.5">
          <Label htmlFor="insights-month">Month</Label>
          <Input
            id="insights-month"
            type="month"
            value={month}
            max={latestMonth(new Date())}
            onChange={(e) => e.target.value && setMonth(e.target.value)}
            className="w-44"
          />
        </div>
        <Button variant="outline" onClick={load}>
          Refresh
        </Button>
        <p className="mb-2 text-xs text-muted-foreground">
          Figures come in fixed weeks. Groups smaller than {insights.min_cohort} are
          never shown, and neither is a figure that would reveal one.
        </p>
      </div>

      <Tabs key={insights.month} defaultValue={firstReady}>
        <TabsList>
          {insights.weeks.map((week) => (
            <TabsTrigger key={week.label} value={week.label} className="gap-2">
              {weekRangeLabel(week)}
              {!week.frozen && <Badge variant="outline">not ready</Badge>}
            </TabsTrigger>
          ))}
        </TabsList>
        {insights.weeks.map((week) => (
          <TabsContent key={week.label} value={week.label} className="pt-4">
            <InsightsWeek week={week} minCohort={insights.min_cohort} />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
