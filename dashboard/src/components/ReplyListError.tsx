import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'

interface ReplyListErrorProps {
  message: string
  onRetry: () => void
}

/** Destructive alert with a Retry button for a failed confession list load. */
export function ReplyListError({ message, onRetry }: ReplyListErrorProps) {
  return (
    <Alert variant="destructive">
      <AlertTitle>Could not load confessions</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
      <AlertAction>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </AlertAction>
    </Alert>
  )
}
