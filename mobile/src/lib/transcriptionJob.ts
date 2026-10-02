// Auri — the native side of waiting for a transcription (plan task 16.2)
//
// Binds transcriptionJobCore's port to fetch and AppState. Timers stop while the
// app is in the background, so a wait also ends as soon as the app is active again:
// the poll resumes at once rather than after the rest of a stale delay.

import { AppState } from 'react-native';
import { ENDPOINTS, REQUEST_TIMEOUT_MS, getApiBaseUrl } from '../config/api';
import type { JobPort } from './transcriptionJobCore';

/** A port for jobs belonging to the device with *deviceTokenHash*. */
export function transcriptionJobPort(deviceTokenHash: string): JobPort {
  return {
    fetchJob: async (jobId, signal) => {
      // Each poll gets its own budget, so a request lost with the connection
      // cannot stall the wait; the cancel signal still ends it at once.
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), REQUEST_TIMEOUT_MS);
      const onCancel = () => abort.abort();
      signal.addEventListener('abort', onCancel);
      try {
        const response = await fetch(`${getApiBaseUrl()}${ENDPOINTS.sttJob(jobId)}`, {
          headers: { 'X-Device-Token-Hash': deviceTokenHash },
          signal: abort.signal,
        });
        let body: unknown = null;
        try {
          body = await response.json();
        } catch (_error: unknown) {
          body = null;
        }
        return { status: response.status, body };
      } finally {
        clearTimeout(timer);
        signal.removeEventListener('abort', onCancel);
      }
    },
    wait: (ms, signal) =>
      new Promise<void>((resolve) => {
        if (signal.aborted) {
          resolve();
          return;
        }
        const done = () => {
          clearTimeout(timer);
          subscription.remove();
          signal.removeEventListener('abort', done);
          resolve();
        };
        const timer = setTimeout(done, ms);
        const subscription = AppState.addEventListener('change', (next) => {
          if (next === 'active') done();
        });
        signal.addEventListener('abort', done);
      }),
    now: () => Date.now(),
  };
}
