import { RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { LiveKitStatus, NgrokStatus } from '@/lib/api'

interface StatusPanelProps {
  ngrok: NgrokStatus | null
  livekit: LiveKitStatus | null
  onRefresh: () => void
  refreshing: boolean
}

/** ngrok tunnel + self-hosted LiveKit reachability, side by side. */
export function StatusPanel({ ngrok, livekit, onRefresh, refreshing }: StatusPanelProps) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">ngrok Tunnel</CardTitle>
          <Badge variant={ngrok?.running ? 'default' : 'destructive'}>
            {ngrok?.running ? 'Running' : 'Stopped'}
          </Badge>
        </CardHeader>
        <CardContent>
          {ngrok?.running && ngrok.public_url ? (
            <a
              href={ngrok.public_url}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-sm text-primary underline underline-offset-2"
            >
              {ngrok.public_url}
            </a>
          ) : (
            <p className="text-sm text-muted-foreground">
              No tunnel detected on localhost:4040 — start ngrok locally.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">LiveKit (self-hosted)</CardTitle>
          <Badge variant={livekit?.reachable ? 'default' : 'destructive'}>
            {livekit?.reachable ? 'Reachable' : 'Unreachable'}
          </Badge>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-sm text-muted-foreground">{livekit?.url}</p>
        </CardContent>
      </Card>

      <Button
        variant="outline"
        size="sm"
        onClick={onRefresh}
        disabled={refreshing}
        className="sm:col-span-2 sm:w-fit"
      >
        <RefreshCw className={refreshing ? 'animate-spin' : ''} />
        Refresh status
      </Button>
    </div>
  )
}
