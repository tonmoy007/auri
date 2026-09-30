import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The real modules reach for Expo native code; the API client only needs an
// identity hash and the ENDPOINTS table, so the native boundary is stubbed out.
vi.mock('expo-secure-store', () => ({}));
vi.mock('./deviceToken', () => ({
  hashDeviceToken: vi.fn(async () => 'device-hash-0123456789abcdef'),
}));

import {
  PRIEST_ASK_TIMEOUT_MS,
  PriestApiError,
  askPriest,
  fetchPriestStatus,
  toPriestErrorInfo,
} from './priestApi';
import { ENDPOINTS, getApiBaseUrl } from '../config/api';
import type { PriestAnswerResponse, PriestStatus } from '../types/priest';

const QUESTION = 'What do the notes say about grief?';

const ANSWER: PriestAnswerResponse = {
  request_id: 'req-1',
  kind: 'not_covered',
  points: [],
  quotes: [],
  reflection: null,
  citations: [],
  notice: 'Not covered.',
  contacts: null,
  disclaimer_version: '1',
  index_version: null,
  prompt_version: null,
};

const STATUS: PriestStatus = {
  enabled: true,
  persona_name: 'Guide',
  traditions: [{ id: 'buddhism', label: 'Buddhism' }],
  disclaimer_version: '1',
  max_question_chars: 1000,
};

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;

function stubFetch(impl: (url: string, init?: RequestInit) => Promise<Response>): FetchMock {
  const mock = vi.fn<typeof fetch>((input, init) => impl(String(input), init));
  vi.stubGlobal('fetch', mock);
  return mock;
}

function sentInit(mock: FetchMock): RequestInit {
  const init = mock.mock.calls[0]?.[1];
  if (!init) throw new Error('fetch was not called');
  return init;
}

async function failureOf(promise: Promise<unknown>): Promise<PriestApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof PriestApiError) return error;
    throw error;
  }
  throw new Error('expected the request to fail');
}

/** A fetch that never answers and rejects the way fetch does when its signal aborts. */
function hangingFetch(): FetchMock {
  return stubFetch(
    (_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () =>
          reject(new DOMException('The operation was aborted.', 'AbortError')),
        );
      }),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('askPriest: the request', () => {
  it('posts the question with the device hash header to the ask endpoint', async () => {
    // Arrange
    const fetchMock = stubFetch(async () => jsonResponse(ANSWER));

    // Act
    await askPriest({ question: QUESTION, tradition: 'buddhism' });

    // Assert
    const init = sentInit(fetchMock);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${getApiBaseUrl()}${ENDPOINTS.priestAsk}`);
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({
      'X-Device-Token-Hash': 'device-hash-0123456789abcdef',
      'Content-Type': 'application/json',
    });
  });

  it('sends exactly question, tradition and language, nothing else', async () => {
    // Arrange
    const fetchMock = stubFetch(async () => jsonResponse(ANSWER));

    // Act
    await askPriest({ question: QUESTION, tradition: 'islam' });

    // Assert
    expect(JSON.parse(String(sentInit(fetchMock).body))).toEqual({
      question: QUESTION,
      tradition: 'islam',
      language: 'en',
    });
  });

  it('leaves the tradition out when there is none', async () => {
    // Arrange
    const fetchMock = stubFetch(async () => jsonResponse(ANSWER));

    // Act
    await askPriest({ question: QUESTION, tradition: null });

    // Assert
    expect(JSON.parse(String(sentInit(fetchMock).body))).toEqual({
      question: QUESTION,
      language: 'en',
    });
  });

  it('trims the question before sending', async () => {
    // Arrange
    const fetchMock = stubFetch(async () => jsonResponse(ANSWER));

    // Act
    await askPriest({ question: `  ${QUESTION}\n`, tradition: null });

    // Assert
    expect(JSON.parse(String(sentInit(fetchMock).body)).question).toBe(QUESTION);
  });

  it('returns the parsed answer', async () => {
    // Arrange
    stubFetch(async () => jsonResponse(ANSWER));

    // Act
    const answer = await askPriest({ question: QUESTION, tradition: null });

    // Assert
    expect(answer).toEqual(ANSWER);
  });

  it('clears its timeout timer once the answer arrives', async () => {
    // Arrange
    stubFetch(async () => jsonResponse(ANSWER));

    // Act
    await askPriest({ question: QUESTION, tradition: null });

    // Assert
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('askPriest: failures', () => {
  it('reports offline when the network request throws', async () => {
    // Arrange
    stubFetch(async () => {
      throw new TypeError('Network request failed');
    });

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.code).toBe('offline');
  });

  it('reports rate_limited with the Retry-After seconds', async () => {
    // Arrange
    stubFetch(async () =>
      jsonResponse({ detail: 'slow down' }, { status: 429, headers: { 'Retry-After': '12' } }),
    );

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect([error.code, error.retryAfterSeconds]).toEqual(['rate_limited', 12]);
  });

  it.each([
    ['is missing', undefined],
    ['is not a number', 'soon'],
    ['is an HTTP date', 'Wed, 21 Oct 2026 07:28:00 GMT'],
  ])('leaves retry seconds null when Retry-After %s', async (_name, header) => {
    // Arrange
    const headers: Record<string, string> = header ? { 'Retry-After': header } : {};
    stubFetch(async () => jsonResponse({ detail: 'x' }, { status: 429, headers }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect([error.code, error.retryAfterSeconds]).toEqual(['rate_limited', null]);
  });

  it.each([
    ['0', 1],
    ['99999', 3600],
  ])('clamps Retry-After %s to %s', async (header, expected) => {
    // Arrange
    stubFetch(async () =>
      jsonResponse({ detail: 'x' }, { status: 429, headers: { 'Retry-After': header } }),
    );

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.retryAfterSeconds).toBe(expected);
  });

  it.each([
    ['priest_mode_disabled', 'disabled'],
    ['priest_busy', 'busy'],
    ['priest_index_unavailable', 'unavailable'],
    ['something_new', 'unavailable'],
  ])('maps a 503 with detail %s to %s', async (detail, code) => {
    // Arrange
    stubFetch(async () => jsonResponse({ detail }, { status: 503 }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.code).toBe(code);
  });

  it('maps 422 to validation', async () => {
    // Arrange
    stubFetch(async () => jsonResponse({ detail: [] }, { status: 422 }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.code).toBe('validation');
  });

  it('maps any other error status to unexpected', async () => {
    // Arrange
    stubFetch(async () => new Response('boom', { status: 500 }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.code).toBe('unexpected');
  });

  it('reports unexpected when a 200 body is not JSON', async () => {
    // Arrange
    stubFetch(async () => new Response('not json', { status: 200 }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.code).toBe('unexpected');
  });

  it('reports unexpected when the answer kind is one the app cannot draw', async () => {
    // Arrange
    stubFetch(async () => jsonResponse({ ...ANSWER, kind: 'sermon' }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.code).toBe('unexpected');
  });

  it('never puts the question in an error message', async () => {
    // Arrange
    stubFetch(async () => new Response('boom', { status: 500 }));

    // Act
    const error = await failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Assert
    expect(error.message).not.toContain('grief');
  });
});

describe('askPriest: timeout and cancel', () => {
  it('gives up after 35 seconds with a timeout error', async () => {
    // Arrange
    hangingFetch();
    const outcome = failureOf(askPriest({ question: QUESTION, tradition: null }));

    // Act
    await vi.advanceTimersByTimeAsync(PRIEST_ASK_TIMEOUT_MS);
    const error = await outcome;

    // Assert
    expect([PRIEST_ASK_TIMEOUT_MS, error.code]).toEqual([35_000, 'timeout']);
  });

  it('does not time out one millisecond early', async () => {
    // Arrange
    const fetchMock = hangingFetch();
    const pending = askPriest({ question: QUESTION, tradition: null }).catch(() => undefined);

    // Act
    await vi.advanceTimersByTimeAsync(PRIEST_ASK_TIMEOUT_MS - 1);
    const signal = sentInit(fetchMock).signal;

    // Assert
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await pending;
  });

  it('reports cancelled, not timeout, when the caller aborts', async () => {
    // Arrange
    hangingFetch();
    const controller = new AbortController();
    const outcome = failureOf(
      askPriest({ question: QUESTION, tradition: null }, { signal: controller.signal }),
    );

    // Act
    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    const error = await outcome;

    // Assert
    expect(error.code).toBe('cancelled');
  });

  it('does not call fetch when the signal is already aborted', async () => {
    // Arrange
    const fetchMock = hangingFetch();
    const controller = new AbortController();
    controller.abort();

    // Act
    const error = await failureOf(
      askPriest({ question: QUESTION, tradition: null }, { signal: controller.signal }),
    );

    // Assert
    expect([error.code, fetchMock.mock.calls.length]).toEqual(['cancelled', 0]);
  });

  it('clears its timeout timer after a cancel', async () => {
    // Arrange
    hangingFetch();
    const controller = new AbortController();
    const outcome = failureOf(
      askPriest({ question: QUESTION, tradition: null }, { signal: controller.signal }),
    );

    // Act
    await vi.advanceTimersByTimeAsync(10);
    controller.abort();
    await outcome;

    // Assert
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('fetchPriestStatus', () => {
  it('reads the status without sending any identity header', async () => {
    // Arrange
    const fetchMock = stubFetch(async () => jsonResponse(STATUS));

    // Act
    const status = await fetchPriestStatus();

    // Assert
    const init = sentInit(fetchMock);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`${getApiBaseUrl()}${ENDPOINTS.priestStatus}`);
    expect(init.method).toBe('GET');
    expect(JSON.stringify(init.headers ?? {})).not.toContain('Device');
    expect(status).toEqual(STATUS);
  });

  it('reports offline when the network request throws', async () => {
    // Arrange
    stubFetch(async () => {
      throw new TypeError('Network request failed');
    });

    // Act
    const error = await failureOf(fetchPriestStatus());

    // Assert
    expect(error.code).toBe('offline');
  });

  it('reports unexpected for a server error', async () => {
    // Arrange
    stubFetch(async () => new Response('boom', { status: 500 }));

    // Act
    const error = await failureOf(fetchPriestStatus());

    // Assert
    expect(error.code).toBe('unexpected');
  });

  it('reports unexpected when the body lacks the status fields', async () => {
    // Arrange
    stubFetch(async () => jsonResponse({ enabled: true }));

    // Act
    const error = await failureOf(fetchPriestStatus());

    // Assert
    expect(error.code).toBe('unexpected');
  });
});

describe('toPriestErrorInfo', () => {
  it('passes a Guide error through with its code and retry seconds', () => {
    // Arrange
    const error = new PriestApiError('rate_limited', 9);

    // Act
    const info = toPriestErrorInfo(error);

    // Assert
    expect(info).toEqual({ code: 'rate_limited', retryAfterSeconds: 9 });
  });

  it('treats any other thrown value as unexpected', () => {
    // Arrange
    const error = new RangeError('bug');

    // Act
    const info = toPriestErrorInfo(error);

    // Assert
    expect(info).toEqual({ code: 'unexpected', retryAfterSeconds: null });
  });
});
