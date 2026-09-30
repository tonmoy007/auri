import { describe, it, expect } from 'vitest';
import {
  CITATION_SHEET_HEADING,
  DISCLAIMER_FOOTER,
  FALLBACK_QUOTE_LABEL,
  REPHRASE_INVITATION,
  STAGE_SWITCH_MS,
  INTRO_STATEMENTS,
  pendingStageAt,
  pendingStageText,
  presentAnswer,
  presentError,
  telUrlFor,
  type DisplayBlock,
} from './priestPresentation';
import type {
  PriestAnswerResponse,
  PriestCitation,
  PriestErrorCode,
} from '../types/priest';

function citation(id: string, title: string, overrides: Partial<PriestCitation> = {}): PriestCitation {
  return {
    id,
    note_title: title,
    heading_path: ['Part one', 'Section two'],
    note_type: 'concept',
    tradition_labels: ['Buddhism'],
    snippet: 'A synthetic snippet for testing.',
    ...overrides,
  };
}

function response(overrides: Partial<PriestAnswerResponse> = {}): PriestAnswerResponse {
  return {
    request_id: 'req-1',
    kind: 'answer',
    points: [],
    quotes: [],
    reflection: null,
    citations: [],
    notice: null,
    contacts: null,
    disclaimer_version: '1',
    index_version: 'v1',
    prompt_version: 'p1',
    ...overrides,
  };
}

function answerResponse(): PriestAnswerResponse {
  return response({
    points: [
      { text: 'First point.', citation_ids: ['S2', 'S1'] },
      { text: 'Second point.', citation_ids: ['S2', 'S9'] },
    ],
    quotes: [{ text: 'A quoted line of text.', citation_id: 'S1', label: 'Quoted in the note "Alpha"' }],
    reflection: 'Be gentle with yourself.',
    citations: [citation('S1', 'Alpha'), citation('S2', 'Beta')],
  });
}

function blockOfType<T extends DisplayBlock['type']>(
  blocks: DisplayBlock[],
  type: T,
): Extract<DisplayBlock, { type: T }> | undefined {
  return blocks.find((b): b is Extract<DisplayBlock, { type: T }> => b.type === type);
}

describe('presentAnswer: answer kind', () => {
  it('numbers citations by their position in the response', () => {
    // Arrange
    const reply = answerResponse();

    // Act
    const presentation = presentAnswer(reply);

    // Assert
    expect(presentation.citations.map((c) => [c.id, c.number])).toEqual([
      ['S1', 1],
      ['S2', 2],
    ]);
  });

  it('gives each point chips numbered by citation position and drops unknown ids', () => {
    // Arrange
    const reply = answerResponse();

    // Act
    const points = blockOfType(presentAnswer(reply).blocks, 'points');

    // Assert
    expect(points?.items[0]?.chips.map((c) => c.number)).toEqual([2, 1]);
    expect(points?.items[1]?.chips.map((c) => c.citationId)).toEqual(['S2']);
  });

  it('labels each chip for screen readers with the note title', () => {
    // Arrange
    const reply = answerResponse();

    // Act
    const points = blockOfType(presentAnswer(reply).blocks, 'points');

    // Assert
    expect(points?.items[0]?.chips[0]?.accessibilityLabel).toBe('Source 2: Beta');
    expect(points?.items[0]?.chips[1]?.accessibilityLabel).toBe('Source 1: Alpha');
  });

  it('uses the label the server sent for a quote, never one of its own', () => {
    // Arrange
    const reply = answerResponse();

    // Act
    const quotes = blockOfType(presentAnswer(reply).blocks, 'quotes');

    // Assert
    expect(quotes?.items).toEqual([
      { text: 'A quoted line of text.', label: 'Quoted in the note "Alpha"', citationId: 'S1' },
    ]);
  });

  it('falls back to a neutral label when the server sent a blank quote label', () => {
    // Arrange
    const reply = answerResponse();
    reply.quotes = [{ text: 'A quoted line of text.', citation_id: 'S1', label: '   ' }];

    // Act
    const quotes = blockOfType(presentAnswer(reply).blocks, 'quotes');

    // Assert
    expect(quotes?.items[0]?.label).toBe(FALLBACK_QUOTE_LABEL);
    expect(FALLBACK_QUOTE_LABEL.toLowerCase()).not.toContain('scripture');
  });

  it('puts the reflection in its own block after the library content', () => {
    // Arrange
    const reply = answerResponse();

    // Act
    const types = presentAnswer(reply).blocks.map((b) => b.type);

    // Assert
    expect(types).toEqual(['points', 'quotes', 'reflection', 'disclaimer']);
  });

  it('omits the reflection block when the reflection is blank', () => {
    // Arrange
    const reply = answerResponse();
    reply.reflection = '  ';

    // Act
    const types = presentAnswer(reply).blocks.map((b) => b.type);

    // Assert
    expect(types).not.toContain('reflection');
  });

  it('shows the server notice (for example a scholar footer) before the disclaimer', () => {
    // Arrange
    const reply = answerResponse();
    reply.notice = 'For a ruling, consult a qualified scholar of your tradition.';

    // Act
    const blocks = presentAnswer(reply).blocks;

    // Assert
    expect(blocks.slice(-2)).toEqual([
      { type: 'notice', text: 'For a ruling, consult a qualified scholar of your tradition.' },
      { type: 'disclaimer', text: DISCLAIMER_FOOTER },
    ]);
  });

  it('announces the number of sources', () => {
    // Arrange
    const reply = answerResponse();

    // Act
    const announcement = presentAnswer(reply).announcement;

    // Assert
    expect(announcement).toBe('Answer with 2 sources');
  });

  it('announces a single source in the singular', () => {
    // Arrange
    const reply = answerResponse();
    reply.citations = [citation('S1', 'Alpha')];

    // Act
    const announcement = presentAnswer(reply).announcement;

    // Assert
    expect(announcement).toBe('Answer with 1 source');
  });

  it('treats an answer with no points and no quotes as not covered', () => {
    // Arrange
    const reply = response({ kind: 'answer' });

    // Act
    const presentation = presentAnswer(reply);

    // Assert
    expect(presentation.blocks.map((b) => b.type)).toEqual(['notice', 'invitation', 'disclaimer']);
  });
});

describe('presentAnswer: citation detail', () => {
  it('builds the sheet content from the citation metadata', () => {
    // Arrange
    const reply = answerResponse();
    reply.citations = [
      citation('S1', 'Alpha', { tradition_labels: ['Buddhism', 'Jainism'] }),
    ];

    // Act
    const detail = presentAnswer(reply).citations[0];

    // Assert
    expect(detail).toEqual({
      id: 'S1',
      number: 1,
      title: 'Alpha',
      headingPath: 'Part one › Section two',
      traditions: 'Buddhism, Jainism',
      snippet: 'A synthetic snippet for testing.',
      accessibilityLabel: 'Source 1: Alpha',
    });
  });

  it('leaves heading path and traditions empty when the note has none', () => {
    // Arrange
    const reply = answerResponse();
    reply.citations = [citation('S1', 'Alpha', { heading_path: [], tradition_labels: [] })];

    // Act
    const detail = presentAnswer(reply).citations[0];

    // Assert
    expect([detail?.headingPath, detail?.traditions]).toEqual(['', '']);
  });

  it('labels the sheet as coming from the study library', () => {
    // Arrange / Act / Assert
    expect(CITATION_SHEET_HEADING).toBe("From Auri's study library");
  });
});

describe('presentAnswer: other kinds', () => {
  it('shows the notice and an invitation to rephrase for not_covered', () => {
    // Arrange
    const reply = response({ kind: 'not_covered', notice: 'The library does not cover this.' });

    // Act
    const presentation = presentAnswer(reply);

    // Assert
    expect(presentation.blocks).toEqual([
      { type: 'notice', text: 'The library does not cover this.' },
      { type: 'invitation', text: REPHRASE_INVITATION },
      { type: 'disclaimer', text: DISCLAIMER_FOOTER },
    ]);
  });

  it('supplies fixed not-covered text when the server sent no notice', () => {
    // Arrange
    const reply = response({ kind: 'not_covered', notice: null });

    // Act
    const notice = blockOfType(presentAnswer(reply).blocks, 'notice');

    // Assert
    expect(notice?.text).toBe("The study library doesn't seem to cover this.");
  });

  it('shows the notice and an invitation to rephrase for deferral', () => {
    // Arrange
    const reply = response({ kind: 'deferral', notice: 'Please talk to a doctor.' });

    // Act
    const presentation = presentAnswer(reply);

    // Assert
    expect(presentation.blocks.map((b) => b.type)).toEqual(['notice', 'invitation', 'disclaimer']);
    expect(blockOfType(presentation.blocks, 'notice')?.text).toBe('Please talk to a doctor.');
  });

  it('shows the excerpts, the notice and an invitation for library_excerpts', () => {
    // Arrange
    const reply = response({
      kind: 'library_excerpts',
      notice: 'Here is what the library says.',
      citations: [citation('S1', 'Alpha'), citation('S2', 'Beta', { snippet: 'Second snippet.' })],
    });

    // Act
    const presentation = presentAnswer(reply);

    // Assert
    const excerpts = blockOfType(presentation.blocks, 'excerpts');
    expect(presentation.blocks.map((b) => b.type)).toEqual([
      'notice',
      'excerpts',
      'invitation',
      'disclaimer',
    ]);
    expect(excerpts?.items[1]).toEqual({
      citationId: 'S2',
      number: 2,
      title: 'Beta',
      snippet: 'Second snippet.',
      accessibilityLabel: 'Source 2: Beta',
    });
  });

  it('announces library excerpts with their source count', () => {
    // Arrange
    const reply = response({
      kind: 'library_excerpts',
      citations: [citation('S1', 'Alpha'), citation('S2', 'Beta'), citation('S3', 'Gamma')],
    });

    // Act
    const announcement = presentAnswer(reply).announcement;

    // Assert
    expect(announcement).toBe('Library excerpts with 3 sources');
  });
});

describe('presentAnswer: crisis', () => {
  it('produces one crisis block with contacts and tel targets from the dial value', () => {
    // Arrange
    const reply = response({
      kind: 'crisis',
      contacts: [
        { label: 'Helpline', detail: '0800 123 456', dial: '0800123456' },
        { label: 'Employee assistance', detail: 'eap@example.test', dial: null },
      ],
    });

    // Act
    const presentation = presentAnswer(reply);

    // Assert
    const crisis = blockOfType(presentation.blocks, 'crisis');
    expect(presentation.blocks).toHaveLength(1);
    expect(crisis?.contacts).toEqual([
      {
        label: 'Helpline',
        detail: '0800 123 456',
        telUrl: 'tel:0800123456',
        accessibilityLabel: 'Call Helpline, 0800 123 456',
      },
      {
        label: 'Employee assistance',
        detail: 'eap@example.test',
        telUrl: null,
        accessibilityLabel: 'Employee assistance, eap@example.test',
      },
    ]);
  });

  it('uses fixed text that does not depend on the server notice', () => {
    // Arrange
    const reply = response({ kind: 'crisis', notice: 'Text the server made up.' });

    // Act
    const crisis = blockOfType(presentAnswer(reply).blocks, 'crisis');

    // Assert
    expect(crisis?.body).toContain('your safety matters');
    expect(crisis?.body).not.toContain('made up');
    expect(crisis?.emergencyLine).toContain('local emergency number');
  });

  it('still produces a crisis block with no contacts when the server sent none', () => {
    // Arrange
    const reply = response({ kind: 'crisis', contacts: null });

    // Act
    const crisis = blockOfType(presentAnswer(reply).blocks, 'crisis');

    // Assert
    expect(crisis?.contacts).toEqual([]);
  });

  it('drops a tel target whose dial value is not strictly digits', () => {
    // Arrange
    const reply = response({
      kind: 'crisis',
      contacts: [{ label: 'Odd', detail: 'odd', dial: '123;rm -rf' }],
    });

    // Act
    const crisis = blockOfType(presentAnswer(reply).blocks, 'crisis');

    // Assert
    expect(crisis?.contacts[0]?.telUrl).toBeNull();
  });

  it('announces that help contacts are shown', () => {
    // Arrange
    const reply = response({ kind: 'crisis' });

    // Act
    const announcement = presentAnswer(reply).announcement;

    // Assert
    expect(announcement).toBe('Important: support contacts are shown.');
  });
});

describe('telUrlFor', () => {
  it.each([
    ['0800123456', 'tel:0800123456'],
    ['+441234567890', 'tel:+441234567890'],
    ['988', 'tel:988'],
  ])('accepts %s', (dial, expected) => {
    // Arrange / Act
    const url = telUrlFor(dial);

    // Assert
    expect(url).toBe(expected);
  });

  it.each([
    ['null', null],
    ['undefined', undefined],
    ['empty', ''],
    ['spaces', '0800 123 456'],
    ['dashes', '+1-800-555'],
    ['a scheme', 'javascript:alert(1)'],
    ['a second plus', '++123'],
    ['a plus inside', '12+34'],
    ['only a plus', '+'],
    ['a newline', '123\n456'],
    ['a ussd code', '*123#'],
    ['too short', '12'],
    ['too long', '1'.repeat(21)],
  ])('rejects %s', (_name, dial) => {
    // Arrange / Act
    const url = telUrlFor(dial);

    // Assert
    expect(url).toBeNull();
  });
});

describe('presentError', () => {
  it('says the question is still there when offline', () => {
    // Arrange
    const info = { code: 'offline' as const, retryAfterSeconds: null };

    // Act
    const error = presentError(info);

    // Assert
    expect(error.message).toBe("You're offline. Your question is still here.");
    expect(error.retryable).toBe(true);
  });

  it('puts the retry seconds in the rate-limit message', () => {
    // Arrange
    const info = { code: 'rate_limited' as const, retryAfterSeconds: 17 };

    // Act
    const error = presentError(info);

    // Assert
    expect(error.message).toBe("Let's pause for a moment. Try again in 17 s.");
  });

  it('rounds fractional retry seconds up and never shows less than 1', () => {
    // Arrange
    const fractional = { code: 'rate_limited' as const, retryAfterSeconds: 2.2 };
    const zero = { code: 'rate_limited' as const, retryAfterSeconds: 0 };

    // Act
    const messages = [presentError(fractional).message, presentError(zero).message];

    // Assert
    expect(messages).toEqual([
      "Let's pause for a moment. Try again in 3 s.",
      "Let's pause for a moment. Try again in 1 s.",
    ]);
  });

  it('uses a generic pause when the rate limit carries no seconds', () => {
    // Arrange
    const info = { code: 'rate_limited' as const, retryAfterSeconds: null };

    // Act
    const error = presentError(info);

    // Assert
    expect(error.message).toBe("Let's pause for a moment. Please try again shortly.");
  });

  it('says the Guide is resting when disabled, and offers a way back instead of retry', () => {
    // Arrange
    const info = { code: 'disabled' as const, retryAfterSeconds: null };

    // Act
    const error = presentError(info);

    // Assert
    expect(error.message).toContain('The Guide is resting');
    expect([error.retryable, error.offersBack]).toEqual([false, true]);
  });

  it.each<[PriestErrorCode, string]>([
    ['busy', 'The Guide is busy right now. Please try again in a moment.'],
    ['timeout', 'That took longer than expected. Please try again.'],
    ['unavailable', "The study library isn't available right now. Please try again later."],
    ['validation', "That question couldn't be sent. Please check it and try again."],
    ['cancelled', 'Stopped. Your question is still here.'],
    ['unexpected', 'Something went wrong. Please try again.'],
  ])('maps %s to its fixed message', (code, message) => {
    // Arrange
    const info = { code, retryAfterSeconds: null };

    // Act
    const error = presentError(info);

    // Assert
    expect(error.message).toBe(message);
  });

  it('marks busy and timeout retryable', () => {
    // Arrange
    const busy = { code: 'busy' as const, retryAfterSeconds: null };
    const timeout = { code: 'timeout' as const, retryAfterSeconds: null };

    // Act
    const flags = [presentError(busy).retryable, presentError(timeout).retryable];

    // Assert
    expect(flags).toEqual([true, true]);
  });
});

describe('pending stage text', () => {
  it('starts on searching and switches to reflecting after two seconds', () => {
    // Arrange
    const justBefore = 1999;

    // Act
    const stages = [pendingStageAt(0), pendingStageAt(justBefore), pendingStageAt(2000)];

    // Assert
    expect(stages).toEqual(['searching', 'searching', 'reflecting']);
    expect(STAGE_SWITCH_MS).toBe(2000);
  });

  it('words the stages without claiming to be server progress', () => {
    // Arrange / Act
    const texts = [pendingStageText('searching'), pendingStageText('reflecting')];

    // Assert
    expect(texts).toEqual(['Searching the library…', 'Reflecting…']);
  });
});

describe('intro statements', () => {
  it('has the four statements in order', () => {
    // Arrange / Act
    const keys = INTRO_STATEMENTS.map((s) => s.key);

    // Assert
    expect(keys).toEqual(['what', 'not', 'privacy', 'crisis']);
  });

  it('says what it is not: clergy, counselling, medical and legal advice', () => {
    // Arrange / Act
    const body = INTRO_STATEMENTS.find((s) => s.key === 'not')?.body ?? '';

    // Assert
    expect(body).toBe('It is not clergy, not counselling, and not medical or legal advice.');
  });

  it('says questions are not stored and not visible to the company', () => {
    // Arrange / Act
    const body = INTRO_STATEMENTS.find((s) => s.key === 'privacy')?.body ?? '';

    // Assert
    expect(body).toBe('Your questions are not stored, and your company cannot see them.');
  });
});
