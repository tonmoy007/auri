// Auri — counselor reply presentation
//
// Pure mapping from what the backend sent for the reply shown after a confession (the
// structured parts, or just the rendered text) to what the response screen draws. No
// React Native imports, so all of it is tested with Vitest. Anything that does not fit
// the expected shape falls back to the plain text, and from there to a fixed message:
// the confessor is never left looking at an empty card.

export const COUNSEL_TONES = ['warm', 'gentle', 'light', 'celebratory', 'steady'] as const;
export type CounselTone = (typeof COUNSEL_TONES)[number];

export const MAX_COUNSEL_SUGGESTIONS = 3;
export const SUGGESTIONS_HEADING = 'Something that might help';
export const FALLBACK_COUNSEL_TEXT =
  "Thank you for trusting this space with what you carried in. Whatever it is, you don't have to hold it alone — it has been heard.";

export interface CounselReply {
  acknowledgement: string;
  reflection: string;
  suggestions: string[];
  closing: string;
  tone: CounselTone;
}

export type CounselPresentation =
  | { kind: 'structured'; prose: string[]; suggestions: string[]; closing: string; tone: CounselTone }
  | { kind: 'plain'; text: string };

function nonEmpty(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function toneOf(value: unknown): CounselTone {
  return COUNSEL_TONES.find((tone) => tone === value) ?? 'warm';
}

function suggestionsOf(value: unknown): string[] | null {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) return null;
  return value
    .flatMap((entry: unknown) => {
      const text = nonEmpty(entry);
      return text ? [text] : [];
    })
    .slice(0, MAX_COUNSEL_SUGGESTIONS);
}

function asObject(raw: unknown): Record<string, unknown> | null {
  const value = typeof raw === 'string' ? safeJson(raw) : raw;
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Read a structured reply from a JSON string or an object, or null when it does not fit.
 *
 * The three prose parts must be non-empty text. Suggestions are optional, blank ones are
 * dropped and at most three are kept. An unknown tone reads as `warm`, since the tone only
 * picks a register and a new one from a newer server should not hide the reply.
 */
export function parseCounselReply(raw: unknown): CounselReply | null {
  const object = asObject(raw);
  if (!object) return null;
  const { acknowledgement, reflection, closing, suggestions: rawSuggestions, tone } = object;
  const parts = [nonEmpty(acknowledgement), nonEmpty(reflection), nonEmpty(closing)];
  const suggestions = suggestionsOf(rawSuggestions);
  if (parts.includes(null) || suggestions === null) return null;
  const [ack, ref, close] = parts as [string, string, string];
  return {
    acknowledgement: ack,
    reflection: ref,
    suggestions,
    closing: close,
    tone: toneOf(tone),
  };
}

/**
 * What the response screen draws: the structured parts when they parse, else the plain
 * text the server rendered, else a fixed message.
 */
export function presentCounsel(
  replyParam: unknown,
  textParam: string | undefined,
): CounselPresentation {
  const reply = parseCounselReply(replyParam);
  if (reply) {
    return {
      kind: 'structured',
      prose: [reply.acknowledgement, reply.reflection],
      suggestions: reply.suggestions,
      closing: reply.closing,
      tone: reply.tone,
    };
  }
  return { kind: 'plain', text: nonEmpty(textParam) ?? FALLBACK_COUNSEL_TEXT };
}
