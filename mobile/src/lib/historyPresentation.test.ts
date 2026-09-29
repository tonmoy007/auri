import { describe, it, expect } from 'vitest';
import {
  HR_REPLY_HEADING,
  formatHistoryDate,
  presentHrReply,
  type HrReplyFields,
} from './historyPresentation';

const REPLIED_AT = '2026-09-29T14:05:00Z';
const EDITED_AT = '2026-09-30T09:30:00Z';
const MAX_REPLY_LENGTH = 2000;

const stubFormatter = (iso: string): string => `<${iso}>`;

function buildFields(overrides: Partial<HrReplyFields> = {}): HrReplyFields {
  return {
    hr_reply: 'We heard you.',
    hr_replied_at: REPLIED_AT,
    hr_reply_edited_at: null,
    ...overrides,
  };
}

describe('presentHrReply', () => {
  it('returns null when there is no reply', () => {
    // Arrange
    const fields = buildFields({ hr_reply: null });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation).toBeNull();
  });

  it('returns null when the reply is only whitespace', () => {
    // Arrange
    const fields = buildFields({ hr_reply: ' \n\t  ' });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation).toBeNull();
  });

  it('returns null when the reply has no saved timestamp', () => {
    // Arrange
    const fields = buildFields({ hr_replied_at: null });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation).toBeNull();
  });

  it('returns null when the backend response has no reply keys at all', () => {
    // Arrange
    const legacyItem = {} as HrReplyFields;

    // Act
    const presentation = presentHrReply(legacyItem, stubFormatter);

    // Assert
    expect(presentation).toBeNull();
  });

  it('uses the static heading with no person or role information', () => {
    // Arrange
    const fields = buildFields();

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(HR_REPLY_HEADING).toBe('Reply from HR');
    expect(presentation?.heading).toBe('Reply from HR');
  });

  it('keeps the body verbatim including newlines and surrounding whitespace', () => {
    // Arrange
    const reply = '  First line\n\nSecond line  \n';
    const fields = buildFields({ hr_reply: reply });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation?.body).toBe(reply);
  });

  it('does not truncate a reply at the maximum length', () => {
    // Arrange
    const reply = 'a'.repeat(MAX_REPLY_LENGTH);
    const fields = buildFields({ hr_reply: reply });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation?.body).toHaveLength(MAX_REPLY_LENGTH);
  });

  it('shows only the first-saved time when the reply was never edited', () => {
    // Arrange
    const fields = buildFields({ hr_reply_edited_at: null });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation?.meta).toBe(`<${REPLIED_AT}>`);
  });

  it('adds an edited marker with its own time when the reply was edited', () => {
    // Arrange
    const fields = buildFields({ hr_reply_edited_at: EDITED_AT });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation?.meta).toBe(`<${REPLIED_AT}> · Edited <${EDITED_AT}>`);
  });

  it('builds an accessibility label from the heading, meta and body', () => {
    // Arrange
    const fields = buildFields({ hr_reply_edited_at: EDITED_AT });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation?.accessibilityLabel).toBe(
      `Reply from HR, <${REPLIED_AT}> · Edited <${EDITED_AT}>. We heard you.`,
    );
  });

  it('returns a URL in the body unchanged', () => {
    // Arrange
    const reply = 'See https://example.com/path?token=abc123 for details';
    const fields = buildFields({ hr_reply: reply });

    // Act
    const presentation = presentHrReply(fields, stubFormatter);

    // Assert
    expect(presentation?.body).toBe(reply);
  });
});

describe('formatHistoryDate', () => {
  it('renders the month and day of the given instant in the requested time zone', () => {
    // Arrange
    const iso = '2026-09-29T14:05:00Z';

    // Act
    const formatted = formatHistoryDate(iso, 'en-US', 'UTC');

    // Assert
    expect(formatted).toMatch(/Sep/);
    expect(formatted).toMatch(/29/);
  });
});
