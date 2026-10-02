import { describe, it, expect } from 'vitest';
import {
  FALLBACK_COUNSEL_TEXT,
  MAX_COUNSEL_SUGGESTIONS,
  parseCounselReply,
  presentCounsel,
} from './counselPresentation';

const REPLY = {
  acknowledgement: 'It sounds like this has weighed on you.',
  reflection: 'Naming it is a real first step.',
  suggestions: ['Tell one person you trust.'],
  closing: 'You have been heard here.',
  tone: 'gentle',
};

describe('parseCounselReply', () => {
  it('reads a structured reply from an object', () => {
    // Act
    const reply = parseCounselReply(REPLY);

    // Assert
    expect(reply).toEqual(REPLY);
  });

  it('reads the same reply from the JSON string the router passes', () => {
    // Arrange
    const raw = JSON.stringify(REPLY);

    // Act
    const reply = parseCounselReply(raw);

    // Assert
    expect(reply?.closing).toBe('You have been heard here.');
  });

  it('trims the text parts and drops blank or non-text suggestions', () => {
    // Arrange
    const raw = { ...REPLY, acknowledgement: '  Thank you.  ', suggestions: ['  Rest.  ', '', '   ', 7] };

    // Act
    const reply = parseCounselReply(raw);

    // Assert
    expect(reply?.acknowledgement).toBe('Thank you.');
    expect(reply?.suggestions).toEqual(['Rest.']);
  });

  it('keeps at most the first three suggestions', () => {
    // Arrange
    const raw = { ...REPLY, suggestions: ['a', 'b', 'c', 'd', 'e'] };

    // Act
    const reply = parseCounselReply(raw);

    // Assert
    expect(reply?.suggestions).toHaveLength(MAX_COUNSEL_SUGGESTIONS);
    expect(reply?.suggestions).toEqual(['a', 'b', 'c']);
  });

  it('treats missing suggestions as none', () => {
    // Arrange
    const { suggestions: _omitted, ...raw } = REPLY;

    // Act
    const reply = parseCounselReply(raw);

    // Assert
    expect(reply?.suggestions).toEqual([]);
  });

  it('reads an unknown tone as warm instead of hiding the reply', () => {
    // Arrange
    const raw = { ...REPLY, tone: 'sparkly' };

    // Act
    const reply = parseCounselReply(raw);

    // Assert
    expect(reply?.tone).toBe('warm');
  });

  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty string', ''],
    ['text that is not JSON', 'You have been heard.'],
    ['a JSON array', '[1, 2]'],
    ['a number', 7],
    ['a missing acknowledgement', { ...REPLY, acknowledgement: undefined }],
    ['a blank reflection', { ...REPLY, reflection: '   ' }],
    ['a non-text closing', { ...REPLY, closing: 3 }],
    ['suggestions that are not a list', { ...REPLY, suggestions: 'rest' }],
  ])('returns null for %s', (_label, raw) => {
    // Act
    const reply = parseCounselReply(raw);

    // Assert
    expect(reply).toBeNull();
  });
});

describe('presentCounsel', () => {
  it('presents the structured parts when they parse', () => {
    // Act
    const presentation = presentCounsel(JSON.stringify(REPLY), 'plain text');

    // Assert
    expect(presentation).toEqual({
      kind: 'structured',
      prose: [REPLY.acknowledgement, REPLY.reflection],
      suggestions: REPLY.suggestions,
      closing: REPLY.closing,
      tone: 'gentle',
    });
  });

  it('falls back to the plain text when there are no usable parts', () => {
    // Act
    const presentation = presentCounsel('{"acknowledgement": "only this"}', '  Rendered text.  ');

    // Assert
    expect(presentation).toEqual({ kind: 'plain', text: 'Rendered text.' });
  });

  it.each([undefined, '', '   '])('falls back to the fixed message for text %j', (text) => {
    // Act
    const presentation = presentCounsel(undefined, text);

    // Assert
    expect(presentation).toEqual({ kind: 'plain', text: FALLBACK_COUNSEL_TEXT });
  });
});
