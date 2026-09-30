import { PriestBuildStatus } from '@/components/PriestBuildStatus'
import { PriestManifestFacts } from '@/components/PriestManifestFacts'
import { PriestVersionList } from '@/components/PriestVersionList'
import { ResourceView } from '@/components/ResourceView'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { usePriestIndex } from '@/hooks/usePriestIndex'

/** The study-note index: what is active, how the last build went, reindex and rollback. */
export function PriestIndexCard() {
  const { index, reindex, starting, activating, startReindex, activate, reload } = usePriestIndex()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Index</CardTitle>
        <CardDescription>
          The searchable copy of the study notes the Guide answers from. Only counts are shown here.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ResourceView state={index} label="the index" onRetry={reload}>
          {(data) => (
            <>
              <PriestManifestFacts index={data} />
              <PriestBuildStatus status={reindex} starting={starting} onStart={startReindex} />
              <PriestVersionList
                versions={data.versions}
                active={data.active_version}
                busyVersion={activating}
                onActivate={activate}
              />
            </>
          )}
        </ResourceView>
      </CardContent>
    </Card>
  )
}
