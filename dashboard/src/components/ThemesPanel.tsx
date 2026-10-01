import { useState } from 'react'
import { ThemesControls } from '@/components/ThemesControls'
import { ThemesResult } from '@/components/ThemesResult'
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useThemesReport } from '@/hooks/useThemesReport'
import { THEME_PERIOD_DAYS } from '@/lib/api'

/** Recurring themes and a leadership digest, built from de-identified summaries only. */
export function ThemesPanel() {
  const { state, generate } = useThemesReport()
  const [days, setDays] = useState<number>(THEME_PERIOD_DAYS[0])
  const busy = state.status === 'loading'

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recurring themes</CardTitle>
        <CardDescription>
          Grouped by the configured model from de-identified summaries, never transcripts (the
          Privacy tab says where that model runs). A theme
          with too few confessions is hidden along with its name, so a small group cannot be
          picked out. The digest you download is exactly what is shown here.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ThemesControls
          days={days}
          busy={busy}
          onDaysChange={setDays}
          onGenerate={() => generate(days)}
        />
        {state.status === 'idle' && (
          <p className="text-sm text-muted-foreground">
            Choose a period and generate. This asks the configured model, so it can take a few
            minutes.
          </p>
        )}
        {busy && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              Grouping confessions with the configured model…
            </p>
            <Skeleton className="h-24 w-full" />
          </div>
        )}
        {state.status === 'error' && (
          <Alert variant="destructive">
            <AlertTitle>Could not generate themes</AlertTitle>
            <AlertDescription>{state.message}</AlertDescription>
            <AlertAction>
              <Button variant="outline" size="sm" onClick={() => generate(days)}>
                Retry
              </Button>
            </AlertAction>
          </Alert>
        )}
        {state.status === 'ready' && <ThemesResult report={state.report} />}
      </CardContent>
    </Card>
  )
}
