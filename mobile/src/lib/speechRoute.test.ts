import { describe, it, expect } from 'vitest';
import { speechToTextPath, transcriptionJobPath } from './speechRoute';

describe('speechToTextPath', () => {
  it('uses the plain route for a confession', () => {
    expect(speechToTextPath('/api/v1/stt')).toBe('/api/v1/stt');
    expect(speechToTextPath('/api/v1/stt', { localOnly: false })).toBe('/api/v1/stt');
  });

  it('asks for local transcription only when a Guide question is spoken', () => {
    // A hosted provider must never hear a person's question
    expect(speechToTextPath('/api/v1/stt', { localOnly: true })).toBe(
      '/api/v1/stt?local_only=true',
    );
  });
});

describe('transcriptionJobPath', () => {
  it('asks for a background job, keeping the local-only rule', () => {
    expect(transcriptionJobPath('/api/v1/stt')).toBe('/api/v1/stt?mode=job');
    expect(transcriptionJobPath('/api/v1/stt', { localOnly: true })).toBe(
      '/api/v1/stt?mode=job&local_only=true',
    );
  });
});
