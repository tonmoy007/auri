// Auri — Guide input rules
//
// Pure logic for the composer, voice input, the home-screen entry point and the
// first-use acknowledgement. No React Native imports, so Vitest covers it.

import type { RecordingStartResult } from '../types';
import {
  TRADITION_IDS,
  TRADITION_LABELS,
  type PriestStatus,
  type TraditionId,
  type TraditionOption,
} from '../types/priest';

// ── The composer ────────────────────────────────────────────────────────

const MIN_QUESTION_CHARS = 3;
const NEAR_LIMIT_FRACTION = 0.9;
const TAB = 9;
const LINE_FEED = 10;
const CARRIAGE_RETURN = 13;
const FIRST_PRINTABLE = 32;
const DELETE_CHAR = 127;

function charCount(text: string): number {
  return Array.from(text).length;
}

function hasControlCharacter(text: string): boolean {
  return Array.from(text).some((ch) => {
    const code = ch.codePointAt(0) ?? FIRST_PRINTABLE;
    const isBreak = code === TAB || code === LINE_FEED || code === CARRIAGE_RETURN;
    return (code < FIRST_PRINTABLE && !isBreak) || code === DELETE_CHAR;
  });
}

/** Whether the server would accept this question: 3 to `maxChars` characters, no control characters. */
export function canSubmitQuestion(text: string, maxChars: number): boolean {
  const trimmed = text.trim();
  const length = charCount(trimmed);
  return length >= MIN_QUESTION_CHARS && length <= maxChars && !hasControlCharacter(trimmed);
}

export function questionCounter(
  text: string,
  maxChars: number,
): { label: string; accessibilityLabel: string; isNearLimit: boolean } {
  const length = charCount(text);
  return {
    label: `${length}/${maxChars}`,
    accessibilityLabel: `${length} of ${maxChars} characters`,
    isNearLimit: length >= maxChars * NEAR_LIMIT_FRACTION,
  };
}

/** The stored tradition preference, if the server still offers it. */
export function resolveTradition(
  preference: string | null,
  offered: TraditionOption[],
): TraditionId | null {
  const known = TRADITION_IDS.find((id) => id === preference);
  if (!known) return null;
  return offered.some((option) => option.id === known) ? known : null;
}

/** Cut text to `maxChars` characters (code points), the same unit the counter and the server use. */
export function clampQuestion(text: string, maxChars: number): string {
  const characters = Array.from(text);
  return characters.length <= maxChars ? text : characters.slice(0, maxChars).join('');
}

/** A notice when the saved tradition is not offered, so the user knows the filter is off. */
export function traditionUnavailableNotice(
  preference: string | null,
  offered: TraditionOption[],
): string | null {
  const known = TRADITION_IDS.find((id) => id === preference);
  if (!known || offered.some((option) => option.id === known)) return null;
  return `Your saved tradition (${TRADITION_LABELS[known]}) isn't available right now, so answers draw on all traditions.`;
}

const MS_PER_SECOND = 1000;

/** Whole seconds still to wait before a retry, counting down from the server's wait. */
export function retryRemainingSeconds(
  startedAtMs: number,
  retryAfterSeconds: number | null,
  nowMs: number,
): number {
  if (retryAfterSeconds === null) return 0;
  const remainingMs = startedAtMs + retryAfterSeconds * MS_PER_SECOND - nowMs;
  return Math.max(0, Math.ceil(remainingMs / MS_PER_SECOND));
}

/**
 * A token that goes stale when bumped. An async result checks the token it
 * started with, so a transcript that lands after Clear is dropped.
 */
export function createGenerationGuard(): {
  current: () => number;
  bump: () => void;
  isCurrent: (token: number) => boolean;
} {
  let generation = 0;
  return {
    current: () => generation,
    bump: () => {
      generation += 1;
    },
    isCurrent: (token) => token === generation,
  };
}

/** Put a transcript into the composer text for review; never sends anything. */
export function mergeTranscript(existing: string, transcript: string, maxChars: number): string {
  const spoken = transcript.trim();
  if (!spoken) return existing;
  const base = existing.trim();
  const joined = base ? `${base} ${spoken}` : spoken;
  return Array.from(joined).slice(0, maxChars).join('');
}

export type VoiceIssue = 'permission' | 'record' | 'transcribe' | 'limit';

const PERMISSION_ERROR_PATTERN = /permission/i;

/** Decide what the recorder's error string means for the user. */
export function classifyRecorderError(
  error: string | null,
  hasPermission: boolean | null,
): VoiceIssue | null {
  if (!error) return null;
  if (hasPermission === false || PERMISSION_ERROR_PATTERN.test(error)) return 'permission';
  return 'record';
}

const TYPE_INSTEAD = 'You can type your question instead.';

/** What a failed start means to the user, read straight from the result so a repeat failure shows again. */
export function voiceIssueForStart(result: RecordingStartResult): VoiceIssue | null {
  if (result === 'permission_denied') return 'permission';
  return result === 'failed' ? 'record' : null;
}

export function voiceIssueMessage(issue: VoiceIssue): string {
  switch (issue) {
    case 'permission':
      return `Microphone access is off. ${TYPE_INSTEAD}`;
    case 'record':
      return `Couldn't record that. ${TYPE_INSTEAD}`;
    case 'limit':
      return 'Recording stopped at the time limit. Check the text before you send.';
    default:
      return `Couldn't turn that into text. ${TYPE_INSTEAD}`;
  }
}

// ── Entry point and first-use acknowledgement ───────────────────────────

/** Whether the home screen should offer the Guide: the server says on, and the user has not hidden it. */
export function shouldShowGuideEntry(status: PriestStatus | null, showGuideMode: boolean): boolean {
  return status !== null && status.enabled && showGuideMode;
}

/** The entry button's text. The persona's name appears inside the conversation instead. */
export function guideEntryLabel(): string {
  return 'Seek guidance';
}

/** What a screen reader says for the entry button: the label, the persona and what it is. */
export function guideEntryAccessibilityLabel(personaName: string): string {
  return `${guideEntryLabel()} from ${personaName}, an AI guide drawing on a study library`;
}

/** SecureStore key holding the last disclaimer version the user accepted. */
export const PRIEST_INTRO_ACK_KEY = 'auri_priest_intro_ack';

export function introAckValue(disclaimerVersion: string): string {
  return `v${disclaimerVersion}`;
}

/** A new disclaimer version is not acknowledged, so the intro shows again. */
export function isIntroAcknowledged(stored: string | null, disclaimerVersion: string): boolean {
  return stored === introAckValue(disclaimerVersion);
}

/** The Guide entry is on unless the user explicitly turned it off. */
export function parseShowGuideMode(stored: string | null): boolean {
  return stored !== 'false';
}

/** A stored tradition preference, dropped if it is not a known tradition id. */
export function parseGuideTradition(stored: string | null): TraditionId | null {
  return TRADITION_IDS.find((id) => id === stored) ?? null;
}
