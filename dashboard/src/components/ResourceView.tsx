import type { ReactNode } from 'react'
import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { ResourceState } from '@/hooks/useAdminResource'

interface ResourceViewProps<T> {
  state: ResourceState<T>
  /** What is being loaded, in words that follow "Loading" and "Could not load". */
  label: string
  onRetry: () => void
  children: (data: T) => ReactNode
}

/** One loaded resource as a skeleton, an error alert with Retry, or its content. */
export function ResourceView<T>({ state, label, onRetry, children }: ResourceViewProps<T>) {
  if (state.status === 'loading') {
    return (
      <div className="space-y-3" role="status" aria-busy="true" aria-label={`Loading ${label}`}>
        <Skeleton className="h-6 w-1/2" />
        <Skeleton className="h-20 w-full" />
      </div>
    )
  }
  if (state.status === 'error') {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load {label}</AlertTitle>
        <AlertDescription>{state.message}</AlertDescription>
        <AlertAction>
          <Button variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        </AlertAction>
      </Alert>
    )
  }
  return <>{children(state.data)}</>
}
