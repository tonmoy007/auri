import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { THEME_PERIOD_DAYS } from '@/lib/api'

interface ThemesControlsProps {
  days: number
  busy: boolean
  onDaysChange: (days: number) => void
  onGenerate: () => void
}

/** Period select and the Generate button. */
export function ThemesControls({ days, busy, onDaysChange, onGenerate }: ThemesControlsProps) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="grid gap-1.5">
        <Label htmlFor="themes-period">Period</Label>
        <Select value={String(days)} onValueChange={(value) => onDaysChange(Number(value))}>
          <SelectTrigger id="themes-period" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {THEME_PERIOD_DAYS.map((option) => (
              <SelectItem key={option} value={String(option)}>
                Last {option} days
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button onClick={onGenerate} disabled={busy}>
        {busy ? 'Generating…' : 'Generate themes'}
      </Button>
    </div>
  )
}
