export interface ConfigEntry {
  key: string
  value: string
  source: 'db' | 'default'
}

export interface ConfigResponse {
  llm: ConfigEntry[]
  stt: ConfigEntry[]
  voice_masks: ConfigEntry[]
  build: ConfigEntry[]
}

export type BuildStatusValue = 'idle' | 'running' | 'success' | 'failed'

export interface BuildStatus {
  status: BuildStatusValue
  log: string
  apk_path: string | null
  started_at: number | null
  finished_at: number | null
}

export interface NgrokStatus {
  running: boolean
  public_url: string | null
}

export interface LiveKitStatus {
  reachable: boolean
  url: string
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(
  baseUrl: string,
  adminKey: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${baseUrl}/api/v1/admin${path}`, {
    ...init,
    headers: {
      'X-Admin-Api-Key': adminKey,
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(body.detail ?? res.statusText, res.status)
  }
  return res.json() as Promise<T>
}

export const adminApi = {
  getConfig: (baseUrl: string, adminKey: string) =>
    request<ConfigResponse>(baseUrl, adminKey, '/config'),

  setConfig: (baseUrl: string, adminKey: string, key: string, value: string) =>
    request<ConfigEntry>(baseUrl, adminKey, '/config', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),

  resetConfig: (baseUrl: string, adminKey: string, key: string) =>
    request<ConfigEntry>(baseUrl, adminKey, `/config/${encodeURIComponent(key)}`, {
      method: 'DELETE',
    }),

  getNgrokStatus: (baseUrl: string, adminKey: string) =>
    request<NgrokStatus>(baseUrl, adminKey, '/ngrok'),

  getLiveKitStatus: (baseUrl: string, adminKey: string) =>
    request<LiveKitStatus>(baseUrl, adminKey, '/livekit'),

  startBuild: (baseUrl: string, adminKey: string, backendUrl: string) =>
    request<BuildStatus>(baseUrl, adminKey, '/build-apk', {
      method: 'POST',
      body: JSON.stringify({ backend_url: backendUrl }),
    }),

  getBuildStatus: (baseUrl: string, adminKey: string) =>
    request<BuildStatus>(baseUrl, adminKey, '/build-apk/status'),
}
