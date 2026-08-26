import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { ConfigEntry } from '@/lib/api'

interface ConfigTableProps {
  title: string
  description: string
  entries: ConfigEntry[]
  onSave: (key: string, value: string) => Promise<void>
  onReset: (key: string) => Promise<void>
}

/** Editable table for one config category (LLM / STT / voice masks) —
 * each row tracks its own draft value so unsaved edits don't fight the
 * next poll's server value until Save is pressed. */
export function ConfigTable({ title, description, entries, onSave, onReset }: ConfigTableProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busyKey, setBusyKey] = useState<string | null>(null)

  const draftFor = (entry: ConfigEntry) => drafts[entry.key] ?? entry.value

  const handleSave = async (entry: ConfigEntry) => {
    setBusyKey(entry.key)
    try {
      await onSave(entry.key, draftFor(entry))
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[entry.key]
        return next
      })
    } finally {
      setBusyKey(null)
    }
  }

  const handleReset = async (entry: ConfigEntry) => {
    setBusyKey(entry.key)
    try {
      await onReset(entry.key)
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[entry.key]
        return next
      })
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <div className="rounded-lg border bg-card">
      <div className="border-b p-4">
        <h3 className="font-medium text-card-foreground">{title}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-56">Key</TableHead>
            <TableHead>Value</TableHead>
            <TableHead className="w-24">Source</TableHead>
            <TableHead className="w-40 text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {entries.map((entry) => {
            const dirty = draftFor(entry) !== entry.value
            const busy = busyKey === entry.key
            return (
              <TableRow key={entry.key}>
                <TableCell className="font-mono text-xs">{entry.key}</TableCell>
                <TableCell>
                  <Input
                    value={draftFor(entry)}
                    onChange={(e) =>
                      setDrafts((prev) => ({ ...prev, [entry.key]: e.target.value }))
                    }
                    className="font-mono text-xs"
                  />
                </TableCell>
                <TableCell>
                  <Badge variant={entry.source === 'db' ? 'default' : 'outline'}>
                    {entry.source}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button
                      size="sm"
                      disabled={!dirty || busy}
                      onClick={() => handleSave(entry)}
                    >
                      Save
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={entry.source === 'default' || busy}
                      onClick={() => handleReset(entry)}
                    >
                      Reset
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
