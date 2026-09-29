import { useId } from 'react'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { HR_REPLY_MAX_LENGTH } from '@/lib/api'

interface ReplyDraftFieldProps {
  value: string
  disabled: boolean
  onChange: (next: string) => void
}

/** The reply textarea with its label and a length counter tied to the server limit. */
export function ReplyDraftField({ value, disabled, onChange }: ReplyDraftFieldProps) {
  const fieldId = useId()
  const counterId = `${fieldId}-counter`
  const overLimit = value.length > HR_REPLY_MAX_LENGTH

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={fieldId}>Your reply</Label>
      <Textarea
        id={fieldId}
        value={value}
        disabled={disabled}
        aria-invalid={overLimit}
        aria-describedby={counterId}
        onChange={(event) => onChange(event.target.value)}
      />
      <p
        id={counterId}
        className={overLimit ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
      >
        {`${value.length} / ${HR_REPLY_MAX_LENGTH}`}
      </p>
    </div>
  )
}
