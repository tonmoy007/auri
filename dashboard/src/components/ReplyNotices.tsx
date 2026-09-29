import { Alert, AlertDescription } from '@/components/ui/alert'

const HELD_NOTICE = 'Held for moderation. You can reply once it has been approved.'
const CRISIS_NOTICE =
  'A reply here is not a welfare response. The person may never open it. Follow the crisis process.'

interface ReplyNoticesProps {
  held: boolean
  crisis: boolean
}

/** Cautions shown above the editor: the crisis warning and the moderation hold. */
export function ReplyNotices({ held, crisis }: ReplyNoticesProps) {
  return (
    <>
      {crisis && (
        <Alert variant="destructive">
          <AlertDescription>{CRISIS_NOTICE}</AlertDescription>
        </Alert>
      )}
      {held && (
        <Alert>
          <AlertDescription>{HELD_NOTICE}</AlertDescription>
        </Alert>
      )}
    </>
  )
}
