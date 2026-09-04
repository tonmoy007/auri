import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, departmentsApi, type DepartmentEntry } from '@/lib/api'

/**
 * The recipient directory: which departments exist and where their
 * confessions are delivered.
 *
 * A department with no chat id is the failure that used to be invisible —
 * confessions forwarded to it sit undelivered while only the bot's log says
 * why. It is called out here instead.
 */
export function DirectoryPanel() {
  const { user, authedRequest } = useAuth()
  const [entries, setEntries] = useState<DepartmentEntry[] | null>(null)
  const [newName, setNewName] = useState('')
  const [newChatId, setNewChatId] = useState('')
  const [busy, setBusy] = useState(false)

  const canWrite = user?.role === 'admin'

  const load = useCallback(async () => {
    try {
      setEntries(await departmentsApi.getDirectory(authedRequest))
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not load the directory')
    }
  }, [authedRequest])

  useEffect(() => {
    load()
  }, [load])

  const run = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true)
    try {
      await work()
      toast.success(done)
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'That change did not go through')
    } finally {
      setBusy(false)
    }
  }

  if (entries === null) return <Skeleton className="h-64 w-full" />

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-card p-4">
        <h3 className="font-medium text-card-foreground">Recipient directory</h3>
        <p className="text-sm text-muted-foreground">
          Departments a confession can be forwarded to, and the Telegram chat each one is
          delivered into. {canWrite ? 'You can edit these.' : 'Read-only for your role.'}
        </p>
      </div>

      {canWrite && (
        <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-card p-4">
          <div className="grid gap-1.5">
            <Label htmlFor="new-department">Department</Label>
            <Input
              id="new-department"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Facilities"
              className="w-52"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-chat-id">Telegram chat id</Label>
            <Input
              id="new-chat-id"
              value={newChatId}
              onChange={(e) => setNewChatId(e.target.value)}
              placeholder="-1001234567890"
              className="w-52 font-mono text-xs"
            />
          </div>
          <Button
            disabled={busy || newName.trim() === ''}
            onClick={() =>
              run(async () => {
                await departmentsApi.create(authedRequest, newName.trim(), newChatId.trim())
                setNewName('')
                setNewChatId('')
              }, 'Department added')
            }
          >
            Add
          </Button>
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Department</TableHead>
            <TableHead>Telegram chat</TableHead>
            <TableHead>Accepting forwards</TableHead>
            <TableHead>Undelivered</TableHead>
            {canWrite && <TableHead className="text-right">Actions</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.length === 0 ? (
            <TableRow>
              <TableCell colSpan={canWrite ? 5 : 4} className="text-muted-foreground">
                No departments configured yet.
              </TableCell>
            </TableRow>
          ) : (
            entries.map((entry) => (
              <DirectoryRow
                key={entry.name}
                entry={entry}
                canWrite={canWrite}
                busy={busy}
                onSave={(chatId, isActive) =>
                  run(
                    () =>
                      departmentsApi.update(authedRequest, entry.name, chatId, isActive),
                    `${entry.name} updated`,
                  )
                }
                onDelete={() =>
                  run(
                    () => departmentsApi.remove(authedRequest, entry.name),
                    `${entry.name} removed`,
                  )
                }
              />
            ))
          )}
        </TableBody>
      </Table>
    </div>
  )
}

interface DirectoryRowProps {
  entry: DepartmentEntry
  canWrite: boolean
  busy: boolean
  onSave: (chatId: string, isActive: boolean) => void
  onDelete: () => void
}

function DirectoryRow({ entry, canWrite, busy, onSave, onDelete }: DirectoryRowProps) {
  const [chatId, setChatId] = useState(entry.telegram_chat_id ?? '')
  const [isActive, setIsActive] = useState(entry.is_active)
  const unroutable = (entry.telegram_chat_id ?? '') === ''

  return (
    <TableRow>
      <TableCell className="font-medium">
        {entry.name}
        {unroutable && (
          <Badge variant="destructive" className="ml-2">
            no chat configured
          </Badge>
        )}
      </TableCell>
      <TableCell>
        {canWrite ? (
          <Input
            value={chatId}
            onChange={(e) => setChatId(e.target.value)}
            placeholder="not set"
            className="w-44 font-mono text-xs"
          />
        ) : (
          <span className="font-mono text-xs">{entry.telegram_chat_id ?? '—'}</span>
        )}
      </TableCell>
      <TableCell>
        {canWrite ? (
          <Switch checked={isActive} onCheckedChange={setIsActive} />
        ) : (
          <span>{entry.is_active ? 'yes' : 'no'}</span>
        )}
      </TableCell>
      <TableCell>{entry.undelivered_count}</TableCell>
      {canWrite && (
        <TableCell className="space-x-2 text-right">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onSave(chatId, isActive)}
          >
            Save
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={busy || entry.undelivered_count > 0}
            title={
              entry.undelivered_count > 0
                ? 'Still has undelivered confessions — deactivate it instead'
                : undefined
            }
            onClick={onDelete}
          >
            Delete
          </Button>
        </TableCell>
      )}
    </TableRow>
  )
}
