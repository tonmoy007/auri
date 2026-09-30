import { Badge } from '@/components/ui/badge'
import type { PriestEndpointHealth, PriestHealth } from '@/lib/api'

type Variant = 'default' | 'destructive' | 'outline'

interface Reading {
  text: string
  variant: Variant
}

function reading(reachable: boolean | null): Reading {
  if (reachable === null) return { text: 'not checked', variant: 'outline' }
  return reachable
    ? { text: 'reachable', variant: 'default' }
    : { text: 'unreachable', variant: 'destructive' }
}

function endpointReading(name: string, endpoint: PriestEndpointHealth): Reading {
  if (!endpoint.configured) return { text: `${name}: not configured`, variant: 'outline' }
  const { text, variant } = reading(endpoint.reachable)
  return { text: `${name} (${endpoint.kind}): ${text}`, variant }
}

/** Whether the chat servers and the embedder answered the last probe. */
export function PriestHealthBadges({ health }: { health: PriestHealth }) {
  const embedder = reading(health.embedder.reachable)
  const items: { id: string; title: string; reading: Reading }[] = [
    { id: 'primary', title: health.primary.host, reading: endpointReading('Primary', health.primary) },
    { id: 'fallback', title: health.fallback.host, reading: endpointReading('Fallback', health.fallback) },
    {
      id: 'embedder',
      title: health.embedder.model,
      reading: { text: `Embedder: ${embedder.text}`, variant: embedder.variant },
    },
  ]
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <Badge key={item.id} variant={item.reading.variant} title={item.title}>
          {item.reading.text}
        </Badge>
      ))}
    </div>
  )
}
