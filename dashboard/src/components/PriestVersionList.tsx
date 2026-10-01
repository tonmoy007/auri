import { useState } from 'react'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

interface PriestVersionListProps {
  versions: string[]
  active: string | null
  busyVersion: string | null
  onActivate: (version: string) => void
}

/** Retained index versions; activating an older one is the rollback. */
export function PriestVersionList({ versions, active, busyVersion, onActivate }: PriestVersionListProps) {
  const [asking, setAsking] = useState<string | null>(null)
  if (versions.length === 0) return null

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">Retained versions</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Version</TableHead>
            <TableHead className="w-32 text-right">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {versions.map((version) => (
            <TableRow key={version}>
              <TableCell className="font-mono text-xs">{version}</TableCell>
              <TableCell className="text-right">
                {version === active ? (
                  <Badge>Active</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyVersion !== null}
                    onClick={() => setAsking(version)}
                  >
                    Activate
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <ConfirmDialog
        open={asking !== null}
        onOpenChange={(open) => !open && setAsking(null)}
        title={`Activate index ${asking ?? ''}?`}
        description="The Guide switches to this version straight away. Switch back by activating the newer one again."
        confirmLabel={`Activate ${asking ?? ''}`}
        onConfirm={() => asking !== null && onActivate(asking)}
      />
    </div>
  )
}
