import { useState } from 'react'
import { toast } from 'sonner'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, hrApi, type ConfessionSummary } from '@/lib/api'

const HTTP_NOT_FOUND = 404

/**
 * Saves an HR reply and reports the outcome.
 *
 * On success the saved summary goes to `onSaved`. A 404 means the confessor
 * deleted the confession while it was open, so the row is dropped through
 * `onMissing`. Any other failure keeps the draft in the editor and only
 * raises a toast.
 */
export function useReplySave(
  onSaved: (saved: ConfessionSummary) => void,
  onMissing: (confessionId: string) => void,
) {
  const { authedRequest } = useAuth()
  const [saving, setSaving] = useState(false)

  const save = async (confessionId: string, reply: string) => {
    setSaving(true)
    try {
      onSaved(await hrApi.writeReply(authedRequest, confessionId, reply))
      toast.success('Reply saved')
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not save the reply'
      if (err instanceof ApiError && err.status === HTTP_NOT_FOUND) onMissing(confessionId)
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

  return { saving, save }
}
