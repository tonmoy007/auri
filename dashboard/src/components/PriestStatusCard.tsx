import { PriestHealthBadges } from '@/components/PriestHealthBadges'
import { PriestKillSwitch } from '@/components/PriestKillSwitch'
import { ResourceView } from '@/components/ResourceView'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAdminResource } from '@/hooks/useAdminResource'
import { usePriestConfig, type PriestSettings } from '@/hooks/usePriestConfig'
import { priestAdminApi } from '@/lib/api'

const NONE_SET = 'None set'

function Facts({ settings }: { settings: PriestSettings }) {
  const rows: [string, string][] = [
    ['Persona name', settings.persona],
    ['Model', settings.model],
    ['Fallback model', settings.fallbackModel || NONE_SET],
  ]
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted-foreground">{term}</dt>
          <dd className="font-mono text-xs">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** The kill switch, the settings it runs with, and whether its servers answer. */
export function PriestStatusCard() {
  const config = usePriestConfig()
  const health = useAdminResource(priestAdminApi.getHealth)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Status</CardTitle>
        <CardDescription>
          Turn the Guide on or off for everyone. Edit the model and limits in the Config tab.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ResourceView state={config.state} label="the Guide status" onRetry={config.reload}>
          {(settings) => (
            <>
              <PriestKillSwitch
                enabled={settings.enabled}
                disabled={config.saving}
                onChange={config.setEnabled}
              />
              <Facts settings={settings} />
            </>
          )}
        </ResourceView>
        <ResourceView state={health.state} label="server reachability" onRetry={health.reload}>
          {(data) => <PriestHealthBadges health={data} />}
        </ResourceView>
      </CardContent>
    </Card>
  )
}
