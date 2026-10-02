import { describe, expect, it } from 'vitest';
import { processingMessage } from './processingMessage';

describe('processingMessage', () => {
  it('shows upload progress, then that the server is transcribing', () => {
    expect(processingMessage('uploading', 0.416, 'Processing…')).toBe('Uploading… 42%');
    expect(processingMessage('transcribing', 1, 'Processing…')).toBe('Transcribing…');
  });

  it('falls back once no transcription is running', () => {
    expect(processingMessage(null, 1, 'Processing…')).toBe('Processing…');
  });
});
