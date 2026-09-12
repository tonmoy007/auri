// Auri — API configuration constants
// Centralized endpoints and connection settings

import * as SecureStore from 'expo-secure-store';

/**
 * Backend URL storage key — exported so `useBackendUrl` reads/writes the
 * same slot this module's in-memory override is hydrated from.
 */
export const BACKEND_URL_STORAGE_KEY = 'auri_backend_url_override';

/**
 * Build-time default base URL — set via `EXPO_PUBLIC_API_URL` at `eas build`
 * time. A dashboard-configured runtime override (see `setBackendUrlOverride`)
 * takes precedence once loaded, so a single APK survives a changing ngrok
 * URL without a rebuild.
 */
const DEFAULT_API_BASE_URL: string =
  process.env['EXPO_PUBLIC_API_URL'] ?? 'http://localhost:8000';

const DEFAULT_WS_URL: string =
  process.env['EXPO_PUBLIC_WS_URL'] ?? 'ws://localhost:8000/ws/confession';

let backendUrlOverride: string | null = null;

/** Derive the WebSocket URL from a base URL (http→ws, https→wss). */
function deriveWsUrl(baseUrl: string): string {
  return `${baseUrl.replace(/^http/, 'ws')}/ws/confession`;
}

/** Current effective API base URL — runtime override if set, else the build-time default. */
export function getApiBaseUrl(): string {
  return backendUrlOverride ?? DEFAULT_API_BASE_URL;
}

/** Current effective WebSocket URL — derived from the same override as {@link getApiBaseUrl}. */
export function getWsUrl(): string {
  return backendUrlOverride ? deriveWsUrl(backendUrlOverride) : DEFAULT_WS_URL;
}

/** The build-time default, ignoring any runtime override — for display in Settings. */
export function getDefaultApiBaseUrl(): string {
  return DEFAULT_API_BASE_URL;
}

/**
 * Hydrate the in-memory override from secure storage. Call once, before the
 * first screen that fetches (root layout) — fetch call sites read
 * `getApiBaseUrl()`/`getWsUrl()` synchronously and don't await this.
 */
export async function loadBackendUrlOverride(): Promise<void> {
  backendUrlOverride = await SecureStore.getItemAsync(BACKEND_URL_STORAGE_KEY);
}

/** Set (or, passing `null`, clear) the runtime backend URL override. */
export async function setBackendUrlOverride(url: string | null): Promise<void> {
  backendUrlOverride = url;
  if (url) {
    await SecureStore.setItemAsync(BACKEND_URL_STORAGE_KEY, url);
  } else {
    await SecureStore.deleteItemAsync(BACKEND_URL_STORAGE_KEY);
  }
}

/**
 * API endpoint paths.
 * All paths are relative to API_BASE_URL.
 */
export const ENDPOINTS = {
  /** Health check */
  health: '/api/v1/health',
  /** Submit a completed confession */
  confessions: '/api/v1/confessions',
  /** Preview an AI summary for a transcript before submitting */
  confessionPreview: '/api/v1/confessions/preview',
  /** Get a specific confession by ID */
  confession: (id: string): string => `/api/v1/confessions/${id}`,
  /** Forward a confession to a recipient department */
  forward: (id: string): string => `/api/v1/confessions/${id}/forward`,
  /** Delete a confession */
  deleteConfession: (id: string): string => `/api/v1/confessions/${id}`,
  /** Transcribe a recorded confession to text */
  stt: '/api/v1/stt',
  /** Apply a voice mask to a recorded confession */
  voiceMask: '/api/v1/voice/mask',
  /** Synthesize an AI agent voice response */
  tts: '/api/v1/tts',
  /** List configured recipient departments for the Forward flow */
  departments: '/api/v1/departments',
} as const;

/**
 * WebSocket event types for real-time communication.
 */
export const WS_EVENTS = {
  /** Client sends audio chunk for processing */
  AUDIO_CHUNK: 'audio:chunk',
  /** Server sends back partial transcript */
  TRANSCRIPT_PARTIAL: 'transcript:partial',
  /** Server sends final transcript */
  TRANSCRIPT_FINAL: 'transcript:final',
  /** Server sends AI-generated summary */
  SUMMARY_READY: 'summary:ready',
  /** Server sends mask processing status */
  MASK_STATUS: 'mask:status',
  /** Error event */
  ERROR: 'error',
} as const;

/**
 * Request timeout in milliseconds, for requests whose cost is fixed.
 */
export const REQUEST_TIMEOUT_MS = 30_000;

/** Slower-than-realtime factor allowed before a transcription is called dead. */
const AUDIO_PROCESSING_TIMEOUT_FACTOR = 4;

/** Ceiling, so a pathological case still fails rather than hanging forever. */
const MAX_AUDIO_PROCESSING_TIMEOUT_MS = 20 * 60_000;

/**
 * Timeout for a request whose server-side work scales with how much audio
 * was recorded (transcription, voice masking).
 *
 * A flat 30s was applied to transcription regardless of length, but
 * transcribing runs slower than realtime. Measured against this stack's
 * `base` Whisper model: 15s of speech took 9.5s (0.63x), 30s took 79s
 * (2.64x), and 5 minutes took 570s (1.9x). So the flat budget was already
 * unreachable for a *thirty second* confession, and hopeless for the
 * 5-minute recording MAX_RECORDING_DURATION_MS explicitly allows.
 *
 * The multiplier carries headroom over the measured worst case; the floor
 * covers upload and model load for short clips.
 */
export function uploadTimeoutMsFor(durationMs: number): number {
  const scaled = durationMs * AUDIO_PROCESSING_TIMEOUT_FACTOR;
  return Math.min(Math.max(scaled, REQUEST_TIMEOUT_MS), MAX_AUDIO_PROCESSING_TIMEOUT_MS);
}

/**
 * Maximum audio recording duration in milliseconds.
 */
export const MAX_RECORDING_DURATION_MS = 300_000; // 5 minutes

/**
 * Audio recording configuration.
 */
export const AUDIO_CONFIG = {
  /** Sample rate for recording */
  sampleRate: 44100,
  /** Number of audio channels */
  channels: 1,
  /** Bit rate in bits per second */
  bitRate: 128_000,
  /** Audio file format */
  format: 'aac' as const,
  /** Quality preset */
  quality: 'high' as const,
} as const;
