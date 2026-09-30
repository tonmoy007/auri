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
  analytics: ConfigEntry[]
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

export interface AuditEvent {
  id: string
  actor_user_id: string
  action: string
  target_confession_id: string | null
  content_tier: 'summary' | 'raw' | null
  justification: string | null
  source_ip: string | null
  detail: string | null
  created_at: string
}

export interface AuditPage {
  items: AuditEvent[]
  total: number
  limit: number
  offset: number
}

export interface Bucket {
  label: string
  /** `null` when the bucket was suppressed — not the same as zero. */
  count: number | null
  suppressed: boolean
}

export interface SentimentPoint {
  label: string
  buckets: Bucket[]
}

export interface Insights {
  range_start: string
  range_end: string
  min_cohort: number
  total: Bucket
  volume_by_day: Bucket[]
  volume_by_week: Bucket[]
  by_category: Bucket[]
  by_sentiment: Bucket[]
  by_department: Bucket[]
  forwarded: Bucket
  blind: Bucket
  flagged: Bucket
  flagged_rate: number | null
  delivered: Bucket
  median_hours_to_delivery: number | null
  sentiment_trend: SentimentPoint[]
}

export type ThemeSentimentStatus = 'ok' | 'suppressed' | 'no_previous_period'

export interface ThemeEntry {
  rank: number
  label: string
  confessions: number
  /** Suppressed when 1 to min_cohort-1 people were in it last period. */
  previous_period: Bucket
  /** Fraction 0-1; `null` when too few members carried a sentiment. */
  negative_share: number | null
  previous_negative_share: number | null
  /** Change in negative share (fraction points); `null` unless status is `ok`. */
  sentiment_change: number | null
  sentiment_status: ThemeSentimentStatus
}

/** How themes were formed: by the local model, by stored category, or not at all. */
export type ThemesMethod = 'model' | 'category' | 'none'

export interface ThemesReport {
  range_start: string
  range_end: string
  days: number
  min_cohort: number
  method: ThemesMethod
  notice: string | null
  analysed: Bucket
  /** True when a period had more confessions than the report reads. */
  truncated: boolean
  themes: ThemeEntry[]
  /** Themes withheld for being smaller than min_cohort; their names are never sent. */
  hidden_themes: number
  digest_markdown: string
  digest_csv: string
}

/** Report periods the Themes tab offers, in days. */
export const THEME_PERIOD_DAYS = [7, 14, 30] as const

export interface PrivacyFact {
  id: string
  statement: string
}

export interface RetentionRun {
  ran_at: string
  /** The windows this run actually enforced, kept in the log as evidence. */
  retention_hours: number
  reply_retention_days: number
  /** Each count is suppressed below the cohort, like every other figure. */
  deleted: Bucket
  emptied_to_shell: Bucket
  expired_replies: Bucket
  expired_devices: Bucket
}

export interface RetentionOverview {
  retention_hours: number
  reply_retention_days: number
  /** How often the job is expected to run; overdue is measured against this. */
  expected_run_hours: number
  /** `null` when the job has never recorded a run. */
  last_run: RetentionRun | null
  /** True when no run is recorded, or the last is older than the expected gap. */
  overdue: boolean
  due_to_delete: Bucket
  due_to_empty: Bucket
  due_to_expire: Bucket
}

export interface StaffMember {
  email: string
  role: UserRole
  last_login_at: string | null
}

export interface StaffOverview {
  role_counts: Record<string, number>
  /** Named accounts: present for administrators only, `null` for HR. */
  members: StaffMember[] | null
}

export interface PrivacyOverview {
  min_cohort: number
  guarantees: PrivacyFact[]
  limits: PrivacyFact[]
  retention: RetentionOverview
  staff: StaffOverview
}

export type ModerationSeverity = 'none' | 'policy' | 'harassment' | 'crisis'

export interface QueueItem {
  id: string
  transcript: string
  ai_summary: string | null
  category: string | null
  sentiment: string | null
  status: string
  severity: ModerationSeverity
  created_at: string
  acknowledged_by: string | null
  acknowledged_at: string | null
  reviewed_by: string | null
  reviewed_at: string | null
}

export interface DeliveryOverviewItem {
  id: string
  recipient_dept: string | null
  recipient_chat_id: string | null
  severity: ModerationSeverity
  created_at: string
  delivered_at: string | null
  /** Why this has not arrived; `null` once it has. */
  blocked_reason: string | null
}

export interface DepartmentEntry {
  name: string
  telegram_chat_id: string | null
  is_active: boolean
  undelivered_count: number
}

export type ConfessionStatus = 'pending' | 'forwarded' | 'deleted' | 'flagged'

/** Longest reply the backend accepts; mirrors the backend's MAX_HR_REPLY_LENGTH. */
export const HR_REPLY_MAX_LENGTH = 2000

/**
 * The summary tier of a confession, as HR sees it.
 *
 * Deliberately has no transcript and no author field: HR replies without
 * reading the raw words, and the reply's author is recorded only in the audit
 * trail. Timestamps are ISO 8601 strings.
 */
export interface ConfessionSummary {
  id: string
  status: ConfessionStatus
  category: string | null
  sentiment: string | null
  severity: ModerationSeverity
  ai_summary: string | null
  recipient_dept: string | null
  created_at: string
  delivered_at: string | null
  hr_reply: string | null
  /** When the reply was first saved; never moves afterwards. */
  hr_replied_at: string | null
  /** When the reply text last changed after the first save; `null` if never edited. */
  hr_reply_edited_at: string | null
}

export interface ConfessionSummaryPage {
  items: ConfessionSummary[]
  total: number
  limit: number
  offset: number
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

export const auditApi = {
  list: (
    request: Requester,
    filters: { action?: string; limit: number; offset: number },
  ) => {
    const query = new URLSearchParams({
      limit: String(filters.limit),
      offset: String(filters.offset),
    })
    if (filters.action) query.set('action', filters.action)
    return request<AuditPage>(`/audit?${query.toString()}`)
  },

  listActions: (request: Requester) => request<string[]>('/audit/actions'),
}

export const hrApi = {
  getInsights: (request: Requester, range: { since: string; until: string }) => {
    const query = new URLSearchParams({ since: range.since, until: range.until })
    return request<Insights>(`/hr/insights?${query.toString()}`)
  },

  getThemes: (request: Requester, days: number) =>
    request<ThemesReport>(`/hr/themes?days=${days}`),

  listConfessions: (
    request: Requester,
    filters: { replied?: boolean; limit: number; offset: number },
  ) => {
    const query = new URLSearchParams({
      limit: String(filters.limit),
      offset: String(filters.offset),
    })
    if (filters.replied !== undefined) query.set('replied', String(filters.replied))
    return request<ConfessionSummaryPage>(`/hr/confessions?${query.toString()}`)
  },

  writeReply: (request: Requester, confessionId: string, reply: string) =>
    request<ConfessionSummary>(`/hr/confessions/${confessionId}/reply`, {
      method: 'PUT',
      body: JSON.stringify({ reply }),
    }),
}

export const privacyApi = {
  getOverview: (request: Requester) => request<PrivacyOverview>('/privacy/overview'),
}

export const moderationApi = {
  getQueue: (request: Requester) => request<QueueItem[]>('/moderation/queue'),

  decide: (request: Requester, confessionId: string, decision: 'approve' | 'reject') =>
    request<QueueItem>(`/moderation/${confessionId}/${decision}`, { method: 'POST' }),

  acknowledge: (request: Requester, confessionId: string) =>
    request<QueueItem>(`/moderation/${confessionId}/acknowledge`, { method: 'POST' }),
}

export const departmentsApi = {
  getDirectory: (request: Requester) =>
    request<DepartmentEntry[]>('/departments/directory'),

  create: (request: Requester, name: string, telegramChatId: string) =>
    request<DepartmentEntry>('/departments/directory', {
      method: 'POST',
      body: JSON.stringify({
        name,
        telegram_chat_id: telegramChatId === '' ? null : telegramChatId,
      }),
    }),

  update: (
    request: Requester,
    name: string,
    telegramChatId: string,
    isActive: boolean,
  ) =>
    request<DepartmentEntry>(`/departments/directory/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({
        telegram_chat_id: telegramChatId === '' ? null : telegramChatId,
        is_active: isActive,
      }),
    }),

  remove: (request: Requester, name: string) =>
    request<void>(`/departments/directory/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
}

export const deliveryApi = {
  getOverview: (request: Requester) =>
    request<DeliveryOverviewItem[]>('/delivery/overview'),

  resend: (request: Requester, confessionId: string) =>
    request<DeliveryOverviewItem>(`/delivery/${confessionId}/resend`, {
      method: 'POST',
    }),
}
