import { useCallback, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { errorMessage, useAdminResource, type ResourceState } from '@/hooks/useAdminResource'
import { useAuth } from '@/hooks/useAuth'
import { adminApi, type ConfigEntry } from '@/lib/api'

export const PRIEST_MODE_KEY = 'PRIEST_MODE_ENABLED'
const TRUTHY = new Set(['true', '1', 'yes', 'on'])

export interface PriestSettings {
  enabled: boolean
  persona: string
  model: string
  fallbackModel: string
}

function valueOf(entries: ConfigEntry[], key: string): string {
  return entries.find((entry) => entry.key === key)?.value ?? ''
}

function toSettings(entries: ConfigEntry[]): PriestSettings {
  return {
    enabled: TRUTHY.has(valueOf(entries, PRIEST_MODE_KEY).trim().toLowerCase()),
    persona: valueOf(entries, 'PRIEST_PERSONA_NAME'),
    model: valueOf(entries, 'PRIEST_LLM_MODEL'),
    fallbackModel: valueOf(entries, 'PRIEST_FALLBACK_MODEL'),
  }
}

/** The Guide's own settings from `/admin/config`, and the kill switch that writes one. */
export function usePriestConfig() {
  const { authedRequest } = useAuth()
  const { state: raw, reload } = useAdminResource(adminApi.getConfig)
  const [saving, setSaving] = useState(false)

  const state = useMemo<ResourceState<PriestSettings>>(
    () =>
      raw.status === 'ready' ? { status: 'ready', data: toSettings(raw.data.priest) } : raw,
    [raw],
  )

  const setEnabled = useCallback(
    async (enabled: boolean) => {
      setSaving(true)
      try {
        await adminApi.setConfig(authedRequest, PRIEST_MODE_KEY, String(enabled))
        toast.success(`The Guide is now ${enabled ? 'on' : 'off'}`)
        reload()
      } catch (err) {
        toast.error(errorMessage(err, 'Could not save the setting'))
      } finally {
        setSaving(false)
      }
    },
    [authedRequest, reload],
  )

  return { state, saving, setEnabled, reload }
}
