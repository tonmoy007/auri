import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
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

/** Flagged-item review, previously reachable only from a Telegram chat. */
export function QueuePanel() {
  const { authedRequest } = useAuth()
  const [queue, setQueue] = useState<QueueItem[] | null>(null)
  const [deciding, setDeciding] = useState<string | null>(null)

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

  const decide = async (item: QueueItem, decision: 'approve' | 'reject') => {
    setDeciding(item.id)
    try {
      await moderationApi.decide(authedRequest, item.id, decision)
      toast.success(decision === 'approve' ? 'Approved' : 'Rejected')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not record that decision')
    } finally {
      setDeciding(null)
    }
  }

  if (queue === null) return <Skeleton className="h-64 w-full" />

  return (
    <div className="space-y-4">
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
        queue.map((item) => (
          <Card key={item.id}>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-base">
                  {item.category ?? 'uncategorised'}
                </CardTitle>
                <Badge variant="destructive">flagged</Badge>
                {item.sentiment && <Badge variant="outline">{item.sentiment}</Badge>}
                <span className="text-xs text-muted-foreground">
                  {new Date(item.created_at).toLocaleString()}
                </span>
              </div>
              <CardDescription>{item.ai_summary ?? 'No summary available.'}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="whitespace-pre-wrap rounded-md bg-muted p-3 text-sm">
                {item.transcript}
              </p>
              <div className="flex gap-2">
                <Button
                  onClick={() => decide(item, 'approve')}
                  disabled={deciding === item.id}
                >
                  Approve
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => decide(item, 'reject')}
                  disabled={deciding === item.id}
                >
                  Reject
                </Button>
              </div>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  )
}
