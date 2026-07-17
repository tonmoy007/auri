// Auri — API configuration constants
// Centralized endpoints and connection settings

/**
 * Base URL for the Auri backend API.
 * In production, this would be set via environment variable.
 */
export const API_BASE_URL: string =
  process.env['EXPO_PUBLIC_API_URL'] ?? 'http://localhost:8000';

/**
 * WebSocket URL for real-time audio streaming.
 */
export const WS_URL: string =
  process.env['EXPO_PUBLIC_WS_URL'] ?? 'ws://localhost:8000/ws/confession';

/**
 * API endpoint paths.
 * All paths are relative to API_BASE_URL.
 */
export const ENDPOINTS = {
  /** Health check */
  health: '/api/v1/health',
  /** Submit a completed confession */
  confessions: '/api/v1/confessions',
  /** Get a specific confession by ID */
  confession: (id: string): string => `/api/v1/confessions/${id}`,
  /** Forward a confession to a recipient department */
  forward: (id: string): string => `/api/v1/confessions/${id}/forward`,
  /** Delete a confession */
  deleteConfession: (id: string): string => `/api/v1/confessions/${id}`,
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
 * Request timeout in milliseconds.
 */
export const REQUEST_TIMEOUT_MS = 30_000;

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
