import { describe, it, expect } from 'vitest';
import {
  canSubmitQuestion,
  clampQuestion,
  classifyRecorderError,
  createGenerationGuard,
  guideEntryAccessibilityLabel,
  guideEntryLabel,
  introAckValue,
  isIntroAcknowledged,
  mergeTranscript,
  parseGuideTradition,
  parseShowGuideMode,
  questionCounter,
  resolveTradition,
  retryRemainingSeconds,
  shouldShowGuideEntry,
  traditionUnavailableNotice,
  voiceIssueForStart,
  voiceIssueMessage,
} from './priestInput';
import type { PriestStatus } from '../types/priest';

const MAX_CHARS = 1000;
describe('question input rules', () => {
  it('allows a question of three characters up to the maximum', () => {
    // Arrange
    const shortest = 'why';
    const longest = 'a'.repeat(MAX_CHARS);

    // Act
    const results = [canSubmitQuestion(shortest, MAX_CHARS), canSubmitQuestion(longest, MAX_CHARS)];

    // Assert
    expect(results).toEqual([true, true]);
  });

  it('refuses a question that is too short, too long or only whitespace', () => {
    // Arrange
    const cases = ['hi', '   ab   ', 'a'.repeat(MAX_CHARS + 1), ''];

    // Act
    const results = cases.map((text) => canSubmitQuestion(text, MAX_CHARS));

    // Assert
    expect(results).toEqual([false, false, false, false]);
  });

  it('refuses a question holding a control character', () => {
    // Arrange
    const question = 'hello\u0000 there';

    // Act
    const allowed = canSubmitQuestion(question, MAX_CHARS);

    // Assert
    expect(allowed).toBe(false);
  });

  it('allows line breaks', () => {
    // Arrange
    const question = 'first line\nsecond line';

    // Act
    const allowed = canSubmitQuestion(question, MAX_CHARS);

    // Assert
    expect(allowed).toBe(true);
  });

  it('counts characters against the maximum', () => {
    // Arrange
    const text = 'a'.repeat(950);

    // Act
    const counter = questionCounter(text, MAX_CHARS);

    // Assert
    expect(counter).toEqual({ label: '950/1000', accessibilityLabel: '950 of 1000 characters', isNearLimit: true });
  });

  it('does not flag a short question as near the limit', () => {
    // Arrange
    const text = 'a'.repeat(10);

    // Act
    const counter = questionCounter(text, MAX_CHARS);

    // Assert
    expect(counter.isNearLimit).toBe(false);
  });
});

describe('resolveTradition', () => {
  const offered = [
    { id: 'buddhism', label: 'Buddhism' },
    { id: 'islam', label: 'Islam' },
  ];

  it('returns the preference when the server offers it', () => {
    // Arrange / Act
    const tradition = resolveTradition('islam', offered);

    // Assert
    expect(tradition).toBe('islam');
  });

  it('returns null for a preference the server does not offer', () => {
    // Arrange / Act
    const tradition = resolveTradition('judaism', offered);

    // Assert
    expect(tradition).toBeNull();
  });

  it('returns null with no preference', () => {
    // Arrange / Act
    const tradition = resolveTradition(null, offered);

    // Assert
    expect(tradition).toBeNull();
  });

  it('returns null for an id that is not a known tradition even if offered', () => {
    // Arrange
    const odd = [{ id: 'made-up', label: 'Made up' }];

    // Act
    const tradition = resolveTradition('made-up', odd);

    // Assert
    expect(tradition).toBeNull();
  });
});

describe('entry point', () => {
  const status: PriestStatus = {
    enabled: true,
    persona_name: 'Sage',
    traditions: [],
    disclaimer_version: '1',
    max_question_chars: MAX_CHARS,
  };

  it('is shown only when the server is enabled and the local setting is on', () => {
    // Arrange
    const disabled = { ...status, enabled: false };

    // Act
    const results = [
      shouldShowGuideEntry(status, true),
      shouldShowGuideEntry(status, false),
      shouldShowGuideEntry(disabled, true),
    ];

    // Assert
    expect(results).toEqual([true, false, false]);
  });

  it('is hidden when the status could not be read', () => {
    // Arrange / Act
    const shown = shouldShowGuideEntry(null, true);

    // Assert
    expect(shown).toBe(false);
  });

  it('is labelled "Seek guidance" whatever the persona is called', () => {
    // Arrange / Act
    const label = guideEntryLabel();

    // Assert — the name is for the screen reader and the conversation, not the button
    expect(label).toBe('Seek guidance');
  });

  it('names the persona in the spoken label', () => {
    // Arrange / Act
    const spoken = guideEntryAccessibilityLabel('Sage');

    // Assert
    expect(spoken).toContain('Seek guidance');
    expect(spoken).toContain('Sage');
    expect(spoken).toContain('AI');
  });
});

describe('intro acknowledgement', () => {
  it('stores the disclaimer version with a v prefix', () => {
    // Arrange / Act
    const value = introAckValue('3');

    // Assert
    expect(value).toBe('v3');
  });

  it('counts only the current version as acknowledged', () => {
    // Arrange / Act
    const results = [
      isIntroAcknowledged('v2', '2'),
      isIntroAcknowledged('v1', '2'),
      isIntroAcknowledged(null, '2'),
      isIntroAcknowledged('2', '2'),
    ];

    // Assert
    expect(results).toEqual([true, false, false, false]);
  });
});

describe('voice input text', () => {
  it('fills an empty composer with the transcript', () => {
    // Arrange / Act
    const merged = mergeTranscript('', '  what is grief  ', MAX_CHARS);

    // Assert
    expect(merged).toBe('what is grief');
  });

  it('appends to what was already typed, separated by a space', () => {
    // Arrange / Act
    const merged = mergeTranscript('I am worried.', 'What should I do', MAX_CHARS);

    // Assert
    expect(merged).toBe('I am worried. What should I do');
  });

  it('cuts the result at the maximum length', () => {
    // Arrange
    const existing = 'a'.repeat(995);

    // Act
    const merged = mergeTranscript(existing, 'bbbbbbbbbb', MAX_CHARS);

    // Assert
    expect(merged).toHaveLength(MAX_CHARS);
  });

  it('keeps the existing text when the transcript is blank', () => {
    // Arrange / Act
    const merged = mergeTranscript('keep me', '   ', MAX_CHARS);

    // Assert
    expect(merged).toBe('keep me');
  });

  it('recognises a permission failure from the recorder', () => {
    // Arrange / Act
    const issues = [
      classifyRecorderError('Microphone permission denied', false),
      classifyRecorderError('Failed to start recording', true),
      classifyRecorderError('Failed to stop recording', true),
      classifyRecorderError(null, false),
    ];

    // Assert
    expect(issues).toEqual(['permission', 'record', 'record', null]);
  });

  it('words each voice problem so typing remains the way forward', () => {
    // Arrange / Act
    const messages = [
      voiceIssueMessage('permission'),
      voiceIssueMessage('record'),
      voiceIssueMessage('transcribe'),
      voiceIssueMessage('limit'),
    ];

    // Assert
    expect(messages).toEqual([
      'Microphone access is off. You can type your question instead.',
      "Couldn't record that. You can type your question instead.",
      "Couldn't turn that into text. You can type your question instead.",
      'Recording stopped at the time limit. Check the text before you send.',
    ]);
  });

  it('turns every recording start result into a voice issue, so a repeat failure is shown again', () => {
    // Arrange / Act
    const issues = [
      voiceIssueForStart('started'),
      voiceIssueForStart('permission_denied'),
      voiceIssueForStart('failed'),
    ];

    // Assert
    expect(issues).toEqual([null, 'permission', 'record']);
  });
});

describe('clampQuestion', () => {
  it('leaves text within the limit alone', () => {
    // Arrange / Act
    const clamped = clampQuestion('hello', 10);

    // Assert
    expect(clamped).toBe('hello');
  });

  it('cuts by characters, so an emoji counts as one like the counter does', () => {
    // Arrange
    const text = '😀'.repeat(6);

    // Act
    const clamped = clampQuestion(text, 5);

    // Assert
    expect(Array.from(clamped)).toHaveLength(5);
    expect(questionCounter(clamped, 5).label).toBe('5/5');
  });
});

describe('createGenerationGuard', () => {
  it('treats a token from before a bump as stale', () => {
    // Arrange
    const guard = createGenerationGuard();
    const before = guard.current();

    // Act
    guard.bump();

    // Assert
    expect([guard.isCurrent(before), guard.isCurrent(guard.current())]).toEqual([false, true]);
  });
});

describe('retryRemainingSeconds', () => {
  it('counts down from the server wait and rounds up', () => {
    // Arrange
    const startedAt = 1_000;

    // Act
    const remaining = [
      retryRemainingSeconds(startedAt, 10, startedAt),
      retryRemainingSeconds(startedAt, 10, startedAt + 2_500),
      retryRemainingSeconds(startedAt, 10, startedAt + 10_000),
      retryRemainingSeconds(startedAt, 10, startedAt + 99_000),
    ];

    // Assert
    expect(remaining).toEqual([10, 8, 0, 0]);
  });

  it('is zero when the server gave no wait', () => {
    // Arrange / Act
    const remaining = retryRemainingSeconds(0, null, 5_000);

    // Assert
    expect(remaining).toBe(0);
  });
});

describe('traditionUnavailableNotice', () => {
  const offered = [{ id: 'buddhism', label: 'Buddhism' }];

  it('is silent when there is no saved tradition or it is offered', () => {
    // Arrange / Act
    const notices = [
      traditionUnavailableNotice(null, offered),
      traditionUnavailableNotice('buddhism', offered),
    ];

    // Assert
    expect(notices).toEqual([null, null]);
  });

  it('says the saved tradition is not available when the server does not offer it', () => {
    // Arrange / Act
    const notice = traditionUnavailableNotice('islam', offered);

    // Assert
    expect(notice).toBe(
      "Your saved tradition (Islam) isn't available right now, so answers draw on all traditions.",
    );
  });
});

describe('stored Guide preferences', () => {
  it('shows the Guide by default and only hides it for an explicit false', () => {
    // Arrange / Act
    const results = [
      parseShowGuideMode(null),
      parseShowGuideMode('true'),
      parseShowGuideMode('false'),
      parseShowGuideMode('garbage'),
    ];

    // Assert
    expect(results).toEqual([true, true, false, true]);
  });

  it('keeps a stored tradition only when it is a known tradition id', () => {
    // Arrange / Act
    const results = [
      parseGuideTradition('jainism'),
      parseGuideTradition('made-up'),
      parseGuideTradition(null),
    ];

    // Assert
    expect(results).toEqual(['jainism', null, null]);
  });
});
