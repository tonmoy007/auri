import { vi } from 'vitest'
import type {
  ConfigEntry,
  PriestHealth,
  PriestIndexInfo,
  PriestManifest,
  PriestReindexStatus,
  PriestUsage,
  Requester,
} from '@/lib/api'

/** A route answers a request with a value, throws if it is an Error, or computes one. */
export type Route = unknown | ((init: RequestInit | undefined) => unknown)

export interface RecordedCall {
  method: string
  path: string
  body: unknown
}

export type FakeRequester = Requester & {
  routes: Record<string, Route>
  calls: RecordedCall[]
  callsTo: (key: string) => RecordedCall[]
}

/** A Requester backed by a route table keyed "METHOD /path"; nothing touches a network. */
export function makeRequester(routes: Record<string, Route>): FakeRequester {
  const calls: RecordedCall[] = []
  const fake = vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    calls.push({ method, path, body })
    const route = fake.routes[`${method} ${path}`]
    if (route === undefined) throw new Error(`unrouted ${method} ${path}`)
    const value = typeof route === 'function' ? route(init) : route
    if (value instanceof Error) throw value
    return value
  }) as unknown as FakeRequester
  fake.routes = routes
  fake.calls = calls
  fake.callsTo = (key) => calls.filter((call) => `${call.method} ${call.path}` === key)
  return fake
}

export const MANIFEST: PriestManifest = {
  version: 'v2',
  created_at: '2026-09-01T10:00:00Z',
  embed_model: 'embed-a',
  embed_digest: 'sha256:abc',
  dim: 768,
  chunk_count: 1234,
  note_count: 210,
  exclusions: { private: 4, too_short: 9 },
  unresolved_links: 7,
  warnings: ['w1', 'w2', 'w3'],
  build_seconds: 42,
  cleaner_version: 'c1',
  chunker_version: 'k1',
}

export const INDEX: PriestIndexInfo = {
  active_version: 'v2',
  manifest: MANIFEST,
  versions: ['v2', 'v1'],
  configured_embed_model: 'embed-a',
}

export const IDLE_STATUS: PriestReindexStatus = {
  state: 'idle',
  started_at: null,
  finished_at: null,
  pid: null,
  notes_total: 0,
  notes_indexed: 0,
  chunks: 0,
  exclusions: {},
  error_code: null,
}

export function status(overrides: Partial<PriestReindexStatus>): PriestReindexStatus {
  return { ...IDLE_STATUS, ...overrides }
}

export const HEALTH: PriestHealth = {
  primary: { configured: true, reachable: true, kind: 'vllm', host: 'gpu.internal' },
  fallback: { configured: true, reachable: false, kind: 'ollama', host: 'localhost' },
  embedder: { reachable: true, model: 'embed-a' },
}

export const USAGE: PriestUsage = {
  since: '2026-09-30T08:00:00Z',
  min_cohort: 5,
  outcomes: [
    { kind: 'answer', count: 31, suppressed: false },
    { kind: 'crisis', count: null, suppressed: true },
  ],
  latency: [{ stage: 'total', p50: 1.5, p95: 0.4 }],
}

export function entry(key: string, value: string, source: ConfigEntry['source'] = 'default'): ConfigEntry {
  return { key, value, source }
}

export const PRIEST_CONFIG: ConfigEntry[] = [
  entry('PRIEST_MODE_ENABLED', 'false'),
  entry('PRIEST_PERSONA_NAME', 'Guide'),
  entry('PRIEST_LLM_MODEL', 'qwen-test'),
  entry('PRIEST_FALLBACK_MODEL', 'llama-test'),
]

export function configResponse(priest: ConfigEntry[] = PRIEST_CONFIG) {
  return { llm: [], stt: [], voice_masks: [], build: [], analytics: [], crisis: [], priest }
}
