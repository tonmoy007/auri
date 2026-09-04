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

export type UserRole = 'admin' | 'hr' | 'moderator'

export interface StaffUser {
  id: string
  email: string
  role: UserRole
  is_active: boolean
  last_login_at: string | null
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: StaffUser
}

/**
 * How a request proves who it is.
 *
 * `accessToken` is a staff session (the normal path). `adminKey` is the
 * Phase 10 shared secret, kept as an admin escape hatch for local dev — the
 * backend accepts either on `/admin/*`.
 */
export interface Credentials {
  accessToken?: string | null
  adminKey?: string | null
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function credentialHeaders(auth: Credentials): Record<string, string> {
  const headers: Record<string, string> = {}
  if (auth.accessToken) headers.Authorization = `Bearer ${auth.accessToken}`
  if (auth.adminKey) headers['X-Admin-Api-Key'] = auth.adminKey
  return headers
}

/** Issue a request against the backend, throwing {@link ApiError} on failure. */
export async function apiRequest<T>(
  baseUrl: string,
  path: string,
  auth: Credentials = {},
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${baseUrl}/api/v1${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...credentialHeaders(auth),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(body.detail ?? res.statusText, res.status)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const authApi = {
  login: (baseUrl: string, email: string, password: string) =>
    apiRequest<TokenPair>(baseUrl, '/auth/login', {}, {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  refresh: (baseUrl: string, refreshToken: string) =>
    apiRequest<TokenPair>(baseUrl, '/auth/refresh', {}, {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  logout: (baseUrl: string, auth: Credentials) =>
    apiRequest<void>(baseUrl, '/auth/logout', auth, { method: 'POST' }),

  me: (baseUrl: string, auth: Credentials) =>
    apiRequest<StaffUser>(baseUrl, '/auth/me', auth),
}

/**
 * Issues an authenticated request against a path under `/api/v1`.
 *
 * Supplied by `useAuth().authedRequest`, so credentials and the
 * refresh-on-401 retry live in exactly one place instead of being threaded
 * through every panel.
 */
export type Requester = <T>(path: string, init?: RequestInit) => Promise<T>

export const adminApi = {
  getConfig: (request: Requester) => request<ConfigResponse>('/admin/config'),

  setConfig: (request: Requester, key: string, value: string) =>
    request<ConfigEntry>('/admin/config', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),

  resetConfig: (request: Requester, key: string) =>
    request<ConfigEntry>(`/admin/config/${encodeURIComponent(key)}`, {
      method: 'DELETE',
    }),

  getNgrokStatus: (request: Requester) => request<NgrokStatus>('/admin/ngrok'),

  getLiveKitStatus: (request: Requester) => request<LiveKitStatus>('/admin/livekit'),

  startBuild: (request: Requester, backendUrl: string) =>
    request<BuildStatus>('/admin/build-apk', {
      method: 'POST',
      body: JSON.stringify({ backend_url: backendUrl }),
    }),

  getBuildStatus: (request: Requester) => request<BuildStatus>('/admin/build-apk/status'),
}
