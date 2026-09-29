import { PrivacyFactList } from '@/components/PrivacyFactList'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { PrivacyFact } from '@/lib/api'

interface PrivacyFactsCardProps {
  title: string
  description: string
  facts: PrivacyFact[]
}

/** A titled card holding one list of privacy statements. */
export function PrivacyFactsCard({ title, description, facts }: PrivacyFactsCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <PrivacyFactList facts={facts} />
      </CardContent>
    </Card>
  )
}
