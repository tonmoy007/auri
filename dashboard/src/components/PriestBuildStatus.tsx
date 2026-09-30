import { useState } from 'react'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { formatWhen } from '@/lib/priestFormat'
import type { PriestReindexState, PriestReindexStatus } from '@/lib/api'

interface PriestBuildStatusProps {
  status: PriestReindexStatus | null
  starting: boolean
  onStart: () => void
}

const VARIANT: Record<PriestReindexState, 'default' | 'destructive' | 'outline'> = {
  idle: 'outline',
  running: 'default',
  succeeded: 'default',
  failed: 'destructive',
}

function percent(status: PriestReindexStatus): number {
  if (status.notes_total <= 0) return 0
  return Math.round((status.notes_indexed / status.notes_total) * 100)
}

/** The last build's state, progress while one runs, and the Reindex button. */
export function PriestBuildStatus({ status, starting, onStart }: PriestBuildStatusProps) {
  const [asking, setAsking] = useState(false)
  const running = status?.state === 'running'

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={() => setAsking(true)} disabled={running || starting}>
          {running ? 'Reindexing…' : 'Reindex'}
        </Button>
        {status && <Badge variant={VARIANT[status.state]}>{status.state}</Badge>}
        {status?.finished_at && (
          <span className="text-sm text-muted-foreground">
            Finished {formatWhen(status.finished_at)}
          </span>
        )}
        {status?.error_code && <span className="font-mono text-xs">{status.error_code}</span>}
      </div>
      {running && status && (
        <div className="space-y-1">
          <Progress value={percent(status)} />
          <p className="text-xs text-muted-foreground">
            {status.notes_indexed} of {status.notes_total} notes
          </p>
        </div>
      )}
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title="Reindex the study notes?"
        description="Rebuilds the index from the current notes and can take several minutes. The Guide keeps answering from the active version meanwhile."
        confirmLabel="Start reindex"
        onConfirm={onStart}
      />
    </div>
  )
}
