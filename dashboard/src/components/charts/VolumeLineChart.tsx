import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import type { Bucket } from '@/lib/api'

interface VolumeLineChartProps {
  buckets: Bucket[]
  minCohort: number
}

const CHART_CONFIG = {
  count: { label: 'Confessions', color: 'var(--chart-2)' },
} satisfies ChartConfig

/**
 * Volume over time.
 *
 * Suppressed points are dropped from the line rather than plotted as zero —
 * a zero would assert "nobody spoke that day", which is not what
 * suppression means. The count of hidden points is stated in words below.
 */
export function VolumeLineChart({ buckets, minCohort }: VolumeLineChartProps) {
  const visible = buckets.filter((bucket) => !bucket.suppressed)
  const hiddenCount = buckets.length - visible.length

  return (
    <div className="space-y-2">
      <ChartContainer config={CHART_CONFIG} className="h-56 w-full">
        <LineChart accessibilityLayer data={visible}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} />
          <ChartTooltip content={<ChartTooltipContent />} />
          <Line
            dataKey="count"
            type="monotone"
            stroke="var(--color-count)"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ChartContainer>

      {hiddenCount > 0 && (
        <p className="text-xs text-muted-foreground">
          {hiddenCount} point(s) hidden to protect anonymity (fewer than {minCohort} each).
        </p>
      )}
    </div>
  )
}
