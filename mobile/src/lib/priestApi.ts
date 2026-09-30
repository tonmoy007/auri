// Auri — Guide API client
//
// A small fetch wrapper for the two Guide endpoints: it attaches the anonymous
// device hash, enforces a timeout, and turns every failure into a PriestApiError
// with a code the screens can show fixed copy for. It never logs, and no error it
// raises carries the question or the answer.

import { ENDPOINTS, getApiBaseUrl } from '../config/api';
import { hashDeviceToken } from './deviceToken';
import {
  PRIEST_ANSWER_KINDS,
  type PriestAnswerResponse,
  type PriestErrorCode,
  type PriestErrorInfo,
  type PriestStatus,
  type TraditionId,
} from '../types/priest';

/** An answer can take a while (retrieval, then generation); give up after this. */
export const PRIEST_ASK_TIMEOUT_MS = 35_000;
/** The status call is a cheap read; a slow one should not hold up the home screen. */
export const PRIEST_STATUS_TIMEOUT_MS = 10_000;

const MIN_RETRY_SECONDS = 1;
const MAX_RETRY_SECONDS = 3600;
const HTTP_TOO_MANY_REQUESTS = 429;
const HTTP_UNPROCESSABLE = 422;
const HTTP_UNAVAILABLE = 503;

const UNAVAILABLE_DETAILS: Readonly<Record<string, PriestErrorCode>> = {
  priest_mode_disabled: 'disabled',
  priest_busy: 'busy',
  priest_index_unavailable: 'unavailable',
};

/** A failed Guide request. The message is the code only: never the question or answer. */
export class PriestApiError extends Error {
  constructor(
    readonly code: PriestErrorCode,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(`Guide request failed: ${code}`);
    this.name = 'PriestApiError';
  }
}

/** Reduce anything thrown by the client to what the presentation layer needs. */
export function toPriestErrorInfo(error: unknown): PriestErrorInfo {
  if (error instanceof PriestApiError) {
    return { code: error.code, retryAfterSeconds: error.retryAfterSeconds };
  }
  return { code: 'unexpected', retryAfterSeconds: null };
}

function parseRetryAfter(header: string | null): number | null {
  if (header === null || !/^\d+$/.test(header.trim())) return null;
  const seconds = Number.parseInt(header.trim(), 10);
  return Math.min(Math.max(seconds, MIN_RETRY_SECONDS), MAX_RETRY_SECONDS);
}

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === 'string' ? body.detail : null;
  } catch (_error: unknown) {
    return null;
  }
}

async function errorFromResponse(response: Response): Promise<PriestApiError> {
  if (response.status === HTTP_TOO_MANY_REQUESTS) {
    return new PriestApiError('rate_limited', parseRetryAfter(response.headers.get('Retry-After')));
  }
  if (response.status === HTTP_UNPROCESSABLE) return new PriestApiError('validation');
  if (response.status !== HTTP_UNAVAILABLE) return new PriestApiError('unexpected');
  const detail = await readDetail(response);
  return new PriestApiError((detail && UNAVAILABLE_DETAILS[detail]) || 'unavailable');
}

function classifyFailure(error: unknown, timedOut: boolean): PriestApiError {
  if (error instanceof PriestApiError) return error;
  if (error instanceof Error && error.name === 'AbortError') {
    return new PriestApiError(timedOut ? 'timeout' : 'cancelled');
  }
  if (error instanceof TypeError) return new PriestApiError('offline');
  return new PriestApiError('unexpected');
}

interface RequestSpec {
  path: string;
  method: 'GET' | 'POST';
  body?: unknown;
  /** Attach the anonymous device hash. The status read needs no identity. */
  withDeviceHash: boolean;
  timeoutMs: number;
  signal?: AbortSignal;
}

async function buildInit(spec: RequestSpec, signal: AbortSignal): Promise<RequestInit> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (spec.withDeviceHash) headers['X-Device-Token-Hash'] = await hashDeviceToken();
  if (spec.body !== undefined) headers['Content-Type'] = 'application/json';
  return {
    method: spec.method,
    headers,
    body: spec.body === undefined ? undefined : JSON.stringify(spec.body),
    signal,
  };
}

async function request(spec: RequestSpec): Promise<unknown> {
  if (spec.signal?.aborted) throw new PriestApiError('cancelled');
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, spec.timeoutMs);
  const abortWithCaller = (): void => controller.abort();
  spec.signal?.addEventListener('abort', abortWithCaller);
  try {
    const init = await buildInit(spec, controller.signal);
    const response = await fetch(`${getApiBaseUrl()}${spec.path}`, init);
    if (!response.ok) throw await errorFromResponse(response);
    return await response.json();
  } catch (error: unknown) {
    throw classifyFailure(error, timedOut);
  } finally {
    clearTimeout(timer);
    spec.signal?.removeEventListener('abort', abortWithCaller);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isAnswer(value: unknown): value is PriestAnswerResponse {
  return (
    isRecord(value) &&
    typeof value['kind'] === 'string' &&
    (PRIEST_ANSWER_KINDS as readonly string[]).includes(value['kind']) &&
    Array.isArray(value['points']) &&
    Array.isArray(value['quotes']) &&
    Array.isArray(value['citations'])
  );
}

function isStatus(value: unknown): value is PriestStatus {
  return (
    isRecord(value) &&
    typeof value['enabled'] === 'boolean' &&
    typeof value['persona_name'] === 'string' &&
    Array.isArray(value['traditions']) &&
    typeof value['disclaimer_version'] === 'string' &&
    typeof value['max_question_chars'] === 'number'
  );
}

/** What the screens hand over to ask a question. */
export interface AskInput {
  question: string;
  tradition?: TraditionId | null;
}

/**
 * Ask the Guide. Throws {@link PriestApiError} on any failure, including a
 * 35 s timeout and a caller abort (`cancelled`).
 */
export async function askPriest(
  input: AskInput,
  options: { signal?: AbortSignal } = {},
): Promise<PriestAnswerResponse> {
  const body = {
    question: input.question.trim(),
    ...(input.tradition ? { tradition: input.tradition } : {}),
    language: 'en',
  };
  const parsed = await request({
    path: ENDPOINTS.priestAsk,
    method: 'POST',
    body,
    withDeviceHash: true,
    timeoutMs: PRIEST_ASK_TIMEOUT_MS,
    signal: options.signal,
  });
  if (!isAnswer(parsed)) throw new PriestApiError('unexpected');
  return parsed;
}

/** Read whether the Guide is on. Sends no identity. */
export async function fetchPriestStatus(options: { signal?: AbortSignal } = {}): Promise<PriestStatus> {
  const parsed = await request({
    path: ENDPOINTS.priestStatus,
    method: 'GET',
    withDeviceHash: false,
    timeoutMs: PRIEST_STATUS_TIMEOUT_MS,
    signal: options.signal,
  });
  if (!isStatus(parsed)) throw new PriestApiError('unexpected');
  return parsed;
}
