import type { PrivacyFact } from '@/lib/api'

interface PrivacyFactListProps {
  facts: PrivacyFact[]
}

/** A plain bulleted list of statements about how data is handled. */
export function PrivacyFactList({ facts }: PrivacyFactListProps) {
  return (
    <ul className="list-disc space-y-2 pl-5 text-sm text-foreground">
      {facts.map((fact) => (
        <li key={fact.id}>{fact.statement}</li>
      ))}
    </ul>
  )
}
