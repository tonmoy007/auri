import { PriestEvalCard } from '@/components/PriestEvalCard'
import { PriestIndexCard } from '@/components/PriestIndexCard'
import { PriestStatusCard } from '@/components/PriestStatusCard'
import { PriestUsageCard } from '@/components/PriestUsageCard'

/**
 * The operator view of the Guide: kill switch, study-note index, evaluation and usage.
 *
 * Shows settings, counts and timings only. It has no question, answer or note
 * text to show, because the backend stores and sends none.
 */
export function PriestPanel() {
  return (
    <div className="space-y-6">
      <PriestStatusCard />
      <PriestIndexCard />
      <PriestEvalCard />
      <PriestUsageCard />
    </div>
  )
}
