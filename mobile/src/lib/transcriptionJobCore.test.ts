import { describe, it, expect } from 'vitest';
import {
  JOB_POLL_DEADLINE_MS,
  pollDelayMs,
  pollTranscriptionJob,
  readJobReply,
  readUploadReply,
  type JobPort,
} from './transcriptionJobCore';

type Reply = { status: number; body: unknown } | Error;

/** A port that answers with *replies* in order and moves a fake clock on each wait. */
function fakePort(replies: Reply[], onWait?: (ms: number) => void) {
  let clock = 0;
  const waits: number[] = [];
  const fetched: string[] = [];
  const port: JobPort = {
    fetchJob: async (jobId) => {
      fetched.push(jobId);
      const reply = replies.shift();
      if (reply === undefined) throw new Error('no more replies');
      if (reply instanceof Error) throw reply;
      return reply;
    },
    wait: async (ms) => {
      waits.push(ms);
      clock += ms;
      onWait?.(ms);
    },
    now: () => clock,
  };
  return { port, waits, fetched, advance: (ms: number) => (clock += ms) };
}

const pending = { status: 200, body: { status: 'pending', transcript: null, detail: null } };

describe('readUploadReply', () => {
  it('takes the job id from a 202', () => {
    expect(readUploadReply(202, { job_id: 'abc', status: 'pending' })).toEqual({ kind: 'job', jobId: 'abc' });
  });

  it('accepts a full transcript from a server without job mode', () => {
    expect(readUploadReply(200, { transcript: 'hello' })).toEqual({ kind: 'transcript', transcript: 'hello' });
  });

  it('calls anything else malformed', () => {
    expect(readUploadReply(202, { job_id: '' })).toEqual({ kind: 'malformed' });
    expect(readUploadReply(202, null)).toEqual({ kind: 'malformed' });
    expect(readUploadReply(200, { job_id: 'abc' })).toEqual({ kind: 'malformed' });
  });
});

describe('readJobReply', () => {
  it('reads each job state', () => {
    expect(readJobReply(200, pending.body)).toEqual({ kind: 'pending' });
    expect(readJobReply(200, { status: 'ready', transcript: 'hi' })).toEqual({ kind: 'ready', transcript: 'hi' });
    expect(readJobReply(200, { status: 'failed', detail: 'no_text' })).toEqual({ kind: 'failed', code: 'no_text' });
  });

  it('treats a 404 as gone, since asking again cannot bring the job back', () => {
    expect(readJobReply(404, { detail: 'job not found' })).toEqual({ kind: 'gone' });
  });

  it('treats a server error or an unreadable reply as worth asking again', () => {
    expect(readJobReply(502, null)).toEqual({ kind: 'unreachable' });
    expect(readJobReply(200, { status: 'ready' })).toEqual({ kind: 'unreachable' });
    expect(readJobReply(200, 'oops')).toEqual({ kind: 'unreachable' });
  });
});

describe('pollDelayMs', () => {
  it('starts quick and settles at three seconds', () => {
    expect([0, 1, 2, 3, 10].map(pollDelayMs)).toEqual([1000, 2000, 3000, 3000, 3000]);
  });
});

describe('pollTranscriptionJob', () => {
  it('polls until the transcript is ready', async () => {
    const { port, fetched } = fakePort([pending, pending, { status: 200, body: { status: 'ready', transcript: 'done' } }]);
    const outcome = await pollTranscriptionJob(port, 'job-1', new AbortController().signal);
    expect(outcome).toEqual({ kind: 'ready', transcript: 'done' });
    expect(fetched).toEqual(['job-1', 'job-1', 'job-1']);
  });

  it('reports a failed job with its code', async () => {
    const { port } = fakePort([{ status: 200, body: { status: 'failed', detail: 'transcription_failed' } }]);
    expect(await pollTranscriptionJob(port, 'j', new AbortController().signal)).toEqual({
      kind: 'failed',
      code: 'transcription_failed',
    });
  });

  it('keeps going through a lost connection and server errors', async () => {
    // The job runs on regardless; the next poll after the connection is back finds it.
    const { port } = fakePort([
      new Error('Network request failed'),
      { status: 503, body: null },
      pending,
      { status: 200, body: { status: 'ready', transcript: 'back' } },
    ]);
    expect(await pollTranscriptionJob(port, 'j', new AbortController().signal)).toEqual({
      kind: 'ready',
      transcript: 'back',
    });
  });

  it('stops at once when the job is gone', async () => {
    const { port, fetched } = fakePort([{ status: 404, body: { detail: 'job not found' } }, pending]);
    expect(await pollTranscriptionJob(port, 'j', new AbortController().signal)).toEqual({ kind: 'gone' });
    expect(fetched).toHaveLength(1);
  });

  it('gives up at the deadline', async () => {
    const replies: Reply[] = Array.from({ length: 1000 }, () => pending);
    const { port } = fakePort(replies);
    expect(await pollTranscriptionJob(port, 'j', new AbortController().signal, 10_000)).toEqual({
      kind: 'timed_out',
    });
  });

  it('counts time spent in the background toward the deadline', async () => {
    // Timers do not run while the app is suspended; the clock does.
    const harness = fakePort([pending, pending]);
    let first = true;
    harness.port.wait = async () => {
      if (first) {
        first = false;
        harness.advance(JOB_POLL_DEADLINE_MS);
      }
    };
    expect(await pollTranscriptionJob(harness.port, 'j', new AbortController().signal)).toEqual({
      kind: 'timed_out',
    });
  });

  it('stops without another poll once cancelled', async () => {
    const abort = new AbortController();
    const { port, fetched } = fakePort([pending, pending, pending], () => {
      if (fetched.length === 2) abort.abort();
    });
    expect(await pollTranscriptionJob(port, 'j', abort.signal)).toEqual({ kind: 'cancelled' });
    expect(fetched).toHaveLength(2);
  });

  it('drops a reply that arrives after the cancel', async () => {
    const abort = new AbortController();
    const { port } = fakePort([]);
    port.fetchJob = async () => {
      abort.abort();
      return { status: 200, body: { status: 'ready', transcript: 'late' } };
    };
    expect(await pollTranscriptionJob(port, 'j', abort.signal)).toEqual({ kind: 'cancelled' });
  });
});
