import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import type { Bucket } from '@/lib/api'

interface SuppressibleBarChartProps {
  buckets: Bucket[]
  minCohort: number
  /** Accessible name for the series, shown in the tooltip. */
  seriesLabel: string
}

const CHART_CONFIG = {
  count: { label: 'Confessions', color: 'var(--chart-1)' },
} satisfies ChartConfig

/**
 * Bar chart that draws suppressed buckets as an explicit protected state.
 *
 * A suppressed bucket is not zero and not missing data — it means people
 * were there but too few to show. Rendering it as a gap would read as
 * "nothing happened", which is a different and false claim, so those
 * buckets are listed underneath the chart in words instead.
 */
export function SuppressibleBarChart({
  buckets,
  minCohort,
  seriesLabel,
}: SuppressibleBarChartProps) {
  const visible = buckets.filter((bucket) => !bucket.suppressed)
  const suppressed = buckets.filter((bucket) => bucket.suppressed)

  return (
    <div className="space-y-2">
      {visible.length > 0 && (
        <ChartContainer config={CHART_CONFIG} className="h-56 w-full">
          <BarChart accessibilityLayer data={visible} aria-label={seriesLabel}>
            <CartesianGrid vertical={false} />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} />
            <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} />
            <ChartTooltip content={<ChartTooltipContent />} />
            <Bar dataKey="count" fill="var(--color-count)" radius={4} />
          </BarChart>
        </ChartContainer>
      )}

      {suppressed.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Hidden to protect anonymity (fewer than {minCohort}):{' '}
          {suppressed.map((bucket) => bucket.label).join(', ')}
        </p>
      )}

      {visible.length === 0 && suppressed.length === 0 && (
        <p className="text-xs text-muted-foreground">No data in this range.</p>
      )}
    </div>
  )
}
