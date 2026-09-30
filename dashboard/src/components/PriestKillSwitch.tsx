import { useState } from 'react'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'

interface PriestKillSwitchProps {
  enabled: boolean
  disabled: boolean
  onChange: (enabled: boolean) => void
}

/** The on/off switch for the Guide. A click only asks; the change is made on confirm. */
export function PriestKillSwitch({ enabled, disabled, onChange }: PriestKillSwitchProps) {
  const [asking, setAsking] = useState(false)
  const next = !enabled

  return (
    <div className="flex items-center gap-3">
      <Switch
        id="priest-mode-switch"
        checked={enabled}
        disabled={disabled}
        onCheckedChange={() => setAsking(true)}
      />
      <Label htmlFor="priest-mode-switch">The Guide is {enabled ? 'on' : 'off'}</Label>
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        title={`Turn the Guide ${next ? 'on' : 'off'}?`}
        description={
          next
            ? 'People will see the Guide in the app and can ask it questions, answered from the study notes.'
            : 'The Guide disappears from the app straight away and any open conversation shows it as unavailable.'
        }
        confirmLabel={next ? 'Turn on' : 'Turn off'}
        onConfirm={() => onChange(next)}
      />
    </div>
  )
}
