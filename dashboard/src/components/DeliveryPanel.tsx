import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
import { ApiError, deliveryApi, type DeliveryOverviewItem } from '@/lib/api'

/**
 * Where forwarded confessions went, and what is still stuck.
 *
 * Metadata only — no transcript, no summary. Answering "did anything I said
 * reach anyone" does not require reading what was said.
 */
export function DeliveryPanel() {
  const { authedRequest } = useAuth()
  const [items, setItems] = useState<DeliveryOverviewItem[] | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setItems(await deliveryApi.getOverview(authedRequest))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not load deliveries')
    }
  }, [authedRequest])

  useEffect(() => {
    load()
  }, [load])

  const resend = async (item: DeliveryOverviewItem) => {
    setBusyId(item.id)
    try {
      await deliveryApi.resend(authedRequest, item.id)
      toast.success('Queued for delivery again')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not queue that resend')
    } finally {
      setBusyId(null)
    }
  }

  if (items === null) return <Skeleton className="h-64 w-full" />

  const stuck = items.filter((item) => item.blocked_reason !== null)

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 rounded-lg border bg-card p-4">
        <div>
          <h3 className="font-medium text-card-foreground">Delivery</h3>
          <p className="text-sm text-muted-foreground">
            {stuck.length === 0
              ? 'Everything forwarded has been delivered.'
              : `${stuck.length} forwarded confession(s) have not arrived yet.`}
          </p>
        </div>
        <Button variant="outline" onClick={load}>
          Refresh
        </Button>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Forwarded</TableHead>
            <TableHead>Department</TableHead>
            <TableHead>State</TableHead>
            <TableHead className="text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-muted-foreground">
                Nothing has been forwarded yet.
              </TableCell>
            </TableRow>
          ) : (
            items.map((item) => (
              <TableRow key={item.id}>
                <TableCell className="whitespace-nowrap font-mono text-xs">
                  {new Date(item.created_at).toLocaleString()}
                </TableCell>
                <TableCell>
                  {item.recipient_dept ?? '—'}
                  {item.severity === 'crisis' && (
                    <Badge variant="destructive" className="ml-2">
                      crisis
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  {item.blocked_reason === null ? (
                    <span className="text-sm">
                      Delivered {new Date(item.delivered_at!).toLocaleString()}
                    </span>
                  ) : (
                    <span className="text-sm text-muted-foreground">
                      {item.blocked_reason}
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  {item.delivered_at !== null && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyId === item.id}
                      onClick={() => resend(item)}
                    >
                      Send again
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}
