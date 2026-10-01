import { Skeleton } from '@/components/ui/skeleton'

const SKELETON_ROW_COUNT = 5

/** Placeholder rows shown while the confession list loads. */
export function ReplyListSkeleton() {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading confessions">
      {Array.from({ length: SKELETON_ROW_COUNT }, (_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  )
}
