import { useCallback, useState } from 'react'

const STORAGE_KEY = 'auri-dashboard-connection'

interface AdminSettings {
  baseUrl: string
  adminKey: string
}

const DEFAULTS: AdminSettings = {
  baseUrl: 'http://localhost:8000',
  adminKey: '',
}

function loadSettings(): AdminSettings {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return DEFAULTS
  try {
    return { ...DEFAULTS, ...(JSON.parse(raw) as Partial<AdminSettings>) }
  } catch {
    return DEFAULTS
  }
}

/** Backend URL + admin key for the dashboard, persisted in localStorage.
 *
 * A local dev tool talking to a local backend — no server-side session,
 * the browser tab itself remembers where to point and what key to send.
 */
export function useAdminSettings() {
  const [settings, setSettings] = useState<AdminSettings>(loadSettings)

  const update = useCallback((next: Partial<AdminSettings>) => {
    setSettings((prev) => {
      const merged = { ...prev, ...next }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
      return merged
    })
  }, [])

  return { ...settings, update }
}
