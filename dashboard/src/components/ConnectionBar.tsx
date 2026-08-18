import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface ConnectionBarProps {
  baseUrl: string
  adminKey: string
  connected: boolean
  onChange: (next: { baseUrl?: string; adminKey?: string }) => void
}

/** Backend URL + admin key inputs, with a live connected/disconnected badge. */
export function ConnectionBar({ baseUrl, adminKey, connected, onChange }: ConnectionBarProps) {
  return (
    <div className="flex flex-wrap items-end gap-4 rounded-lg border bg-card p-4">
      <div className="grid gap-1.5">
        <Label htmlFor="backend-url">Backend URL</Label>
        <Input
          id="backend-url"
          value={baseUrl}
          onChange={(e) => onChange({ baseUrl: e.target.value })}
          placeholder="http://localhost:8000"
          className="w-64"
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="admin-key">Admin API Key</Label>
        <Input
          id="admin-key"
          type="password"
          value={adminKey}
          onChange={(e) => onChange({ adminKey: e.target.value })}
          placeholder="X-Admin-Api-Key"
          className="w-64"
        />
      </div>
      <Badge variant={connected ? 'default' : 'destructive'} className="mb-0.5">
        {connected ? 'Connected' : 'Disconnected'}
      </Badge>
    </div>
  )
}
