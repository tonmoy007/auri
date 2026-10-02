// Auri — waiting for a background transcription (plan task 16.2)
//
// Transcribing five minutes of speech takes minutes, and holding one request open
// for all of it froze the booth with no way out. The upload now returns a job id
// at once (`POST /stt?mode=job`) and the app polls `GET /stt/jobs/{id}` until the
// transcript is ready. The rules for reading the replies and for how long to keep
// asking live here, behind a small port, so they can be tested without a network
// or React Native.

/** What the upload's reply means. */
export type UploadReply =
  | { kind: 'job'; jobId: string }
  /** A server without job mode ignores `mode=job` and answers in full. */
  | { kind: 'transcript'; transcript: string }
  | { kind: 'malformed' };

/** One poll's answer. */
export type JobPoll =
  | { kind: 'pending' }
  | { kind: 'ready'; transcript: string }
  /** The server gave up on the audio; the code is fixed (`transcription_failed`, `no_text`). */
  | { kind: 'failed'; code: string }
  /** 404: expired, already read, or never this device's. Asking again cannot help. */
  | { kind: 'gone' }
  /** No connection, or a server error: worth asking again. */
  | { kind: 'unreachable' };

/** How a wait ended. */
export type JobOutcome =
  | { kind: 'ready'; transcript: string }
  | { kind: 'failed'; code: string }
  | { kind: 'gone' }
  | { kind: 'cancelled' }
  | { kind: 'timed_out' };

export interface JobPort {
  /** One `GET` of the job; rejects only when no reply arrived at all. */
  fetchJob: (jobId: string, signal: AbortSignal) => Promise<{ status: number; body: unknown }>;
  /**
   * Wait about *ms*. May return early (the app came back to the foreground, or
   * *signal* aborted); must not reject.
   */
  wait: (ms: number, signal: AbortSignal) => Promise<void>;
  now: () => number;
}

/**
 * Stop asking after this long. Equal to how long the server keeps an unfinished
 * job (25 minutes, `stt_jobs` max pending): past it the job is gone anyway.
 */
export const JOB_POLL_DEADLINE_MS = 25 * 60_000;

/** Wait before the *n*th poll (from 0): 1 s, 2 s, then every 3 s. */
export function pollDelayMs(n: number): number {
  return Math.min(1000 * (n + 1), 3000);
}

/** The fields either reply may carry, each still unchecked. */
interface ReplyFields {
  job_id?: unknown;
  status?: unknown;
  transcript?: unknown;
  detail?: unknown;
}

function isRecord(value: unknown): value is ReplyFields {
  return typeof value === 'object' && value !== null;
}

/** Read the reply to the upload. */
export function readUploadReply(status: number, body: unknown): UploadReply {
  if (!isRecord(body)) return { kind: 'malformed' };
  if (status === 202 && typeof body.job_id === 'string' && body.job_id) {
    return { kind: 'job', jobId: body.job_id };
  }
  if (status === 200 && typeof body.transcript === 'string') {
    return { kind: 'transcript', transcript: body.transcript };
  }
  return { kind: 'malformed' };
}

/** Read one poll's reply. */
export function readJobReply(status: number, body: unknown): JobPoll {
  if (status === 404) return { kind: 'gone' };
  if (status !== 200 || !isRecord(body)) return { kind: 'unreachable' };
  if (body.status === 'pending') return { kind: 'pending' };
  if (body.status === 'ready' && typeof body.transcript === 'string') {
    return { kind: 'ready', transcript: body.transcript };
  }
  if (body.status === 'failed') {
    return { kind: 'failed', code: typeof body.detail === 'string' ? body.detail : 'transcription_failed' };
  }
  return { kind: 'unreachable' };
}

/**
 * Poll *jobId* until it finishes, the deadline passes, or *signal* aborts.
 *
 * A lost connection or a server error does not end the wait: the job keeps
 * running on the server, so the next poll may well find it ready. Only a 404
 * (the job is gone) or a finished job ends it early.
 */
export async function pollTranscriptionJob(
  port: JobPort,
  jobId: string,
  signal: AbortSignal,
  deadlineMs: number = JOB_POLL_DEADLINE_MS,
): Promise<JobOutcome> {
  const deadline = port.now() + deadlineMs;
  for (let n = 0; ; n++) {
    await port.wait(pollDelayMs(n), signal);
    if (signal.aborted) return { kind: 'cancelled' };
    if (port.now() >= deadline) return { kind: 'timed_out' };
    let poll: JobPoll;
    try {
      const reply = await port.fetchJob(jobId, signal);
      poll = readJobReply(reply.status, reply.body);
    } catch (_error: unknown) {
      poll = { kind: 'unreachable' };
    }
    if (signal.aborted) return { kind: 'cancelled' };
    if (poll.kind === 'ready' || poll.kind === 'failed' || poll.kind === 'gone') return poll;
  }
}
