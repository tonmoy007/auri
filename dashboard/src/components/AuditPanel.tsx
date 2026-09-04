import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
import { ApiError, auditApi, type AuditEvent } from '@/lib/api'

const PAGE_SIZE = 25
const ALL_ACTIONS = 'all'

/** Admin-only view of who read or changed what confession content. */
export function AuditPanel() {
  const { authedRequest } = useAuth()
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [actions, setActions] = useState<string[]>([])
  const [action, setAction] = useState<string>(ALL_ACTIONS)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)

  const load = useCallback(async () => {
    try {
      const page = await auditApi.list(authedRequest, {
        action: action === ALL_ACTIONS ? undefined : action,
        limit: PAGE_SIZE,
        offset,
      })
      setEvents(page.items)
      setTotal(page.total)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not load the audit trail')
    }
  }, [authedRequest, action, offset])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    auditApi
      .listActions(authedRequest)
      .then(setActions)
      .catch(() => setActions([]))
  }, [authedRequest])

  const lastPageOffset = Math.max(0, Math.floor((total - 1) / PAGE_SIZE) * PAGE_SIZE)

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div>
        <h3 className="font-medium text-card-foreground">Audit trail</h3>
        <p className="text-sm text-muted-foreground">
          Append-only. Every staff read of confession content is recorded here and can never be
          edited or deleted — this is the evidence behind the anonymity promise.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="grid gap-1.5">
          <Label htmlFor="audit-action">Action</Label>
          <Select
            value={action}
            onValueChange={(value) => {
              setAction(value)
              setOffset(0)
            }}
          >
            <SelectTrigger id="audit-action" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_ACTIONS}>All actions</SelectItem>
              {actions.map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" onClick={load}>
          Refresh
        </Button>
        <span className="mb-2 text-sm text-muted-foreground">{total} event(s)</span>
      </div>

      {events === null ? (
        <Skeleton className="h-48 w-full" />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Action</TableHead>
              <TableHead>Tier</TableHead>
              <TableHead>Confession</TableHead>
              <TableHead>Justification</TableHead>
              <TableHead>Source IP</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {events.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground">
                  No audit events recorded yet.
                </TableCell>
              </TableRow>
            ) : (
              events.map((event) => (
                <TableRow key={event.id}>
                  <TableCell className="whitespace-nowrap font-mono text-xs">
                    {new Date(event.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell>{event.action}</TableCell>
                  <TableCell>
                    {event.content_tier && (
                      <Badge variant={event.content_tier === 'raw' ? 'destructive' : 'outline'}>
                        {event.content_tier}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {event.target_confession_id?.slice(0, 8) ?? '—'}
                  </TableCell>
                  <TableCell className="max-w-xs truncate">
                    {event.justification ?? '—'}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{event.source_ip ?? '—'}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          disabled={offset >= lastPageOffset}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}
