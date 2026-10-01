// Auri — fetching a masked recording as a file (plan task 16.4)
//
// The mask endpoint used to return the masked WAV as base64 JSON, about 33.6 MB for
// a five-minute recording, held in memory before being written out. It now returns
// a one-time download id, and the file is streamed straight to disk. The download
// itself goes through a small port so these rules can be tested without the
// native module.

import { ENDPOINTS } from '../config/api';

/** Starts one download; `cancel` stops it and must not reject. */
export interface DownloadPort {
  start: (
    url: string,
    fileUri: string,
    headers: Record<string, string>,
  ) => { result: Promise<{ status: number }>; cancel: () => Promise<void> };
}

/** The mask endpoint, asking for a download id rather than base64. */
export function maskUploadUrl(baseUrl: string): string {
  return `${baseUrl}${ENDPOINTS.voiceMask}?delivery=download`;
}

/** Where the masked file with *downloadId* is fetched from (once). */
export function maskedDownloadUrl(baseUrl: string, downloadId: string): string {
  return `${baseUrl}${ENDPOINTS.voiceMasked(downloadId)}`;
}

/**
 * Download to *fileUri*; `true` only for a 200. A download still running after
 * *timeoutMs*, or when *signal* aborts (the user cancelled), is cancelled and
 * reported as failed, so the booth never hangs on "Processing…".
 */
export async function downloadMaskedAudio(
  port: DownloadPort,
  url: string,
  fileUri: string,
  headers: Record<string, string>,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<boolean> {
  if (signal?.aborted) return false;
  const { result, cancel } = port.start(url, fileUri, headers);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let onAbort: (() => void) | undefined;
  const stopped = new Promise<'stop'>((resolve) => {
    timer = setTimeout(() => resolve('stop'), timeoutMs);
    onAbort = () => resolve('stop');
    signal?.addEventListener('abort', onAbort);
  });
  try {
    const outcome = await Promise.race([result, stopped]);
    if (outcome === 'stop') {
      await cancel();
      return false;
    }
    return outcome.status === 200;
  } catch (_error: unknown) {
    return false;
  } finally {
    clearTimeout(timer);
    if (onAbort) signal?.removeEventListener('abort', onAbort);
  }
}
