import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, moderationApi, type QueueItem } from '@/lib/api'

/** How long a flagged crisis item may sit unacknowledged before it reads as overdue. */
const CRISIS_SLA_MINUTES = 30

function minutesSince(timestamp: string): number {
  return Math.floor((Date.now() - new Date(timestamp).getTime()) / 60_000)
}

function formatWaiting(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return hours < 24 ? `${hours} h` : `${Math.floor(hours / 24)} d`
}

/**
 * Flagged-item review, previously reachable only from a Telegram chat.
 *
 * Crisis items are pinned by the backend and carry a live SLA timer here:
 * the question that matters while someone may be in danger is not "was this
 * approved" but "has anyone looked yet".
 */
export function QueuePanel() {
  const { authedRequest } = useAuth()
  const [queue, setQueue] = useState<QueueItem[] | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setQueue(await moderationApi.getQueue(authedRequest))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not load the queue')
    }
  }, [authedRequest])

  useEffect(() => {
    load()
  }, [load])

  const run = async (item: QueueItem, work: () => Promise<unknown>, done: string) => {
    setBusyId(item.id)
    try {
      await work()
      toast.success(done)
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'That action did not go through')
    } finally {
      setBusyId(null)
    }
  }

  if (queue === null) return <Skeleton className="h-64 w-full" />

  const unacknowledgedCrisis = queue.filter(
    (item) => item.severity === 'crisis' && item.acknowledged_at === null,
  )

  return (
    <div className="space-y-4">
      {unacknowledgedCrisis.length > 0 && (
        <Alert variant="destructive">
          <AlertTitle>
            {unacknowledgedCrisis.length} crisis item(s) waiting for a human
          </AlertTitle>
          <AlertDescription>
            These describe possible self-harm or violence. They are pinned to the top of
            the queue and will not be delivered anywhere until someone acknowledges them.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex items-start justify-between gap-4 rounded-lg border bg-card p-4">
        <div>
          <h3 className="font-medium text-card-foreground">Moderation queue</h3>
          <p className="text-sm text-muted-foreground">
            Items the safety check flagged for a human. Your decision is recorded against
            your account and written to the audit trail.
          </p>
        </div>
        <Button variant="outline" onClick={load}>
          Refresh
        </Button>
      </div>

      {queue.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is waiting for review.</p>
      ) : (
        queue.map((item) => {
          const isCrisis = item.severity === 'crisis'
          const waiting = minutesSince(item.created_at)
          const overdue = isCrisis && item.acknowledged_at === null && waiting > CRISIS_SLA_MINUTES

          return (
            <Card key={item.id} className={isCrisis ? 'border-destructive' : undefined}>
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle className="text-base">
                    {item.category ?? 'uncategorised'}
                  </CardTitle>
                  <Badge variant={isCrisis ? 'destructive' : 'secondary'}>
                    {item.severity}
                  </Badge>
                  {item.sentiment && <Badge variant="outline">{item.sentiment}</Badge>}
                  <span
                    className={
                      overdue
                        ? 'text-xs font-medium text-destructive'
                        : 'text-xs text-muted-foreground'
                    }
                  >
                    waiting {formatWaiting(waiting)}
                    {overdue && ` — past the ${CRISIS_SLA_MINUTES} min target`}
                  </span>
                  {isCrisis && item.acknowledged_at && (
                    <Badge variant="outline">acknowledged</Badge>
                  )}
                </div>
                <CardDescription>
                  {item.ai_summary ?? 'No summary available.'}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="whitespace-pre-wrap rounded-md bg-muted p-3 text-sm">
                  {item.transcript}
                </p>
                <div className="flex flex-wrap gap-2">
                  {isCrisis && item.acknowledged_at === null && (
                    <Button
                      variant="destructive"
                      disabled={busyId === item.id}
                      onClick={() =>
                        run(
                          item,
                          () => moderationApi.acknowledge(authedRequest, item.id),
                          'Acknowledged',
                        )
                      }
                    >
                      I am handling this
                    </Button>
                  )}
                  <Button
                    disabled={busyId === item.id}
                    onClick={() =>
                      run(
                        item,
                        () => moderationApi.decide(authedRequest, item.id, 'approve'),
                        'Approved',
                      )
                    }
                  >
                    Approve
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busyId === item.id}
                    onClick={() =>
                      run(
                        item,
                        () => moderationApi.decide(authedRequest, item.id, 'reject'),
                        'Rejected',
                      )
                    }
                  >
                    Reject
                  </Button>
                </div>
              </CardContent>
            </Card>
          )
        })
      )}
    </div>
  )
}
