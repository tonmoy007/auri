// Auri — the booth's status line while a recording is processed (plan task 16.2)

/**
 * What the booth says while it processes a recording: upload progress, then
 * that the server is transcribing, then *fallback* once neither is running
 * (masking can outlast the transcript).
 */
export function processingMessage(
  phase: 'uploading' | 'transcribing' | null,
  uploadProgress: number,
  fallback: string,
): string {
  if (phase === 'uploading') return `Uploading… ${Math.round(uploadProgress * 100)}%`;
  if (phase === 'transcribing') return 'Transcribing…';
  return fallback;
}
