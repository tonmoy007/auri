// Auri — Guide presentation logic
//
// Pure mapping from what the Guide's API returns (or how it failed) to what the
// screens draw: display blocks, citation labels, accessibility labels and fixed
// user-facing copy. No React Native imports, so all of it is tested with Vitest.
//
// Two rules shape this file. Labels on quotes come from the server (built there
// from note metadata), never from here, so the app cannot claim "scripture says".
// And the crisis card's words are fixed in this file: a crisis reply must not
// depend on text the server, or a model behind it, produced.

import {
  type CrisisContactOut,
  type PriestAnswerResponse,
  type PriestCitation,
  type PriestErrorInfo,
} from '../types/priest';

// ── Fixed copy ──────────────────────────────────────────────────────────

export const CITATION_SHEET_HEADING = "From Auri's study library";
export const DISCLAIMER_FOOTER =
  'AI summary of a study library. Not clergy, counselling, medical or legal advice.';
export const FALLBACK_QUOTE_LABEL = 'From the study library';
export const REPHRASE_INVITATION = 'You can rephrase your question, or ask about something nearby.';
export const NOT_COVERED_NOTICE = "The study library doesn't seem to cover this.";
export const DEFERRAL_NOTICE =
  'This is something a qualified person is better placed to help with.';
export const EXCERPTS_NOTICE =
  "I couldn't put together a reliable answer, so here are passages from the library.";
export const CRISIS_HEADING = 'You are not alone';
export const CRISIS_BODY =
  'What you shared sounds really heavy, and your safety matters more than anything ' +
  'else here. If you can, reach out to someone you trust or to a local crisis line. ' +
  'You do not have to carry this alone.';
export const CRISIS_EMERGENCY_LINE =
  'If you are in immediate danger, contact your local emergency number.';
export const EMPTY_CONVERSATION_TEXT =
  "Ask a question and I'll share what Auri's study library says, with its sources.";
export const PRIVACY_LINE = 'Guide questions are not stored.';

export const INTRO_STATEMENTS: readonly { key: string; heading: string; body: string }[] = [
  {
    key: 'what',
    heading: 'What this is',
    body: "An AI that answers from Auri's study library of world religions, and shows you the notes it used.",
  },
  {
    key: 'not',
    heading: 'What this is not',
    body: 'It is not clergy, not counselling, and not medical or legal advice.',
  },
  {
    key: 'privacy',
    heading: 'Privacy',
    body: 'Your questions are not stored, and your company cannot see them.',
  },
  {
    key: 'crisis',
    heading: 'If you need help now',
    body: CRISIS_EMERGENCY_LINE,
  },
];

// ── Answers ─────────────────────────────────────────────────────────────

export interface CitationChip {
  citationId: string;
  number: number;
  accessibilityLabel: string;
}

export interface CitationPresentation {
  id: string;
  number: number;
  title: string;
  /** Headings joined for display; empty when the note has none. */
  headingPath: string;
  /** Tradition labels joined for display; empty when none. */
  traditions: string;
  snippet: string;
  accessibilityLabel: string;
}

export interface CrisisContactPresentation {
  label: string;
  detail: string;
  /** A `tel:` URL, only when the server's dial value is strictly digits (and a leading +). */
  telUrl: string | null;
  accessibilityLabel: string;
}

export type DisplayBlock =
  | {
      type: 'points';
      items: { text: string; chips: CitationChip[] }[];
    }
  | {
      type: 'quotes';
      items: { text: string; label: string; citationId: string }[];
    }
  | { type: 'reflection'; heading: string; text: string }
  | { type: 'notice'; text: string }
  | { type: 'invitation'; text: string }
  | {
      type: 'excerpts';
      items: {
        citationId: string;
        number: number;
        title: string;
        snippet: string;
        accessibilityLabel: string;
      }[];
    }
  | {
      type: 'crisis';
      heading: string;
      body: string;
      emergencyLine: string;
      contacts: CrisisContactPresentation[];
    }
  | { type: 'disclaimer'; text: string };

export type CrisisBlock = Extract<DisplayBlock, { type: 'crisis' }>;

export interface AnswerPresentation {
  kind: PriestAnswerResponse['kind'];
  blocks: DisplayBlock[];
  citations: CitationPresentation[];
  /** Spoken by the screen reader when the answer arrives. */
  announcement: string;
}

const DIAL_PATTERN = /^\+?[0-9]{3,20}$/;
const REFLECTION_HEADING = 'Reflection';

/**
 * A `tel:` URL for a server-supplied dial value, or null when it is not safe.
 *
 * Only digits and one leading plus pass, so nothing the server sends can turn
 * the link into anything but a call to a number.
 */
export function telUrlFor(dial: string | null | undefined): string | null {
  return dial && DIAL_PATTERN.test(dial) ? `tel:${dial}` : null;
}

function sourceLabel(number: number, title: string): string {
  return `Source ${number}: ${title}`;
}

function presentCitation(citation: PriestCitation, index: number): CitationPresentation {
  const number = index + 1;
  return {
    id: citation.id,
    number,
    title: citation.note_title,
    headingPath: citation.heading_path.join(' › '),
    traditions: citation.tradition_labels.join(', '),
    snippet: citation.snippet,
    accessibilityLabel: sourceLabel(number, citation.note_title),
  };
}

function presentPoints(
  reply: PriestAnswerResponse,
  citations: CitationPresentation[],
): DisplayBlock | null {
  if (reply.points.length === 0) return null;
  const items = reply.points.map((point) => ({
    text: point.text,
    chips: point.citation_ids.flatMap((id) => {
      const found = citations.find((c) => c.id === id);
      return found
        ? [{ citationId: id, number: found.number, accessibilityLabel: found.accessibilityLabel }]
        : [];
    }),
  }));
  return { type: 'points', items };
}

function presentQuotes(reply: PriestAnswerResponse): DisplayBlock | null {
  if (reply.quotes.length === 0) return null;
  return {
    type: 'quotes',
    items: reply.quotes.map((quote) => ({
      text: quote.text,
      label: quote.label.trim() || FALLBACK_QUOTE_LABEL,
      citationId: quote.citation_id,
    })),
  };
}

function presentContact(contact: CrisisContactOut): CrisisContactPresentation {
  const telUrl = telUrlFor(contact.dial);
  const spoken = `${contact.label}, ${contact.detail}`;
  return {
    label: contact.label,
    detail: contact.detail,
    telUrl,
    accessibilityLabel: telUrl ? `Call ${spoken}` : spoken,
  };
}

function presentCrisis(reply: PriestAnswerResponse): AnswerPresentation {
  return {
    kind: 'crisis',
    blocks: [
      {
        type: 'crisis',
        heading: CRISIS_HEADING,
        body: CRISIS_BODY,
        emergencyLine: CRISIS_EMERGENCY_LINE,
        contacts: (reply.contacts ?? []).map(presentContact),
      },
    ],
    citations: [],
    announcement: 'Important: support contacts are shown.',
  };
}

function noticeBlock(reply: PriestAnswerResponse, fallback: string): DisplayBlock {
  return { type: 'notice', text: reply.notice?.trim() || fallback };
}

const DISCLAIMER_BLOCK: DisplayBlock = { type: 'disclaimer', text: DISCLAIMER_FOOTER };
const INVITATION_BLOCK: DisplayBlock = { type: 'invitation', text: REPHRASE_INVITATION };

function presentDeclined(
  reply: PriestAnswerResponse,
  fallbackNotice: string,
  announcement: string,
): AnswerPresentation {
  return {
    kind: reply.kind,
    blocks: [noticeBlock(reply, fallbackNotice), INVITATION_BLOCK, DISCLAIMER_BLOCK],
    citations: [],
    announcement,
  };
}

function presentExcerpts(
  reply: PriestAnswerResponse,
  citations: CitationPresentation[],
): AnswerPresentation {
  const excerpts: DisplayBlock = {
    type: 'excerpts',
    items: citations.map((c) => ({
      citationId: c.id,
      number: c.number,
      title: c.title,
      snippet: c.snippet,
      accessibilityLabel: c.accessibilityLabel,
    })),
  };
  return {
    kind: reply.kind,
    blocks: [noticeBlock(reply, EXCERPTS_NOTICE), excerpts, INVITATION_BLOCK, DISCLAIMER_BLOCK],
    citations,
    announcement: `Library excerpts with ${sourceCount(citations.length)}`,
  };
}

function sourceCount(count: number): string {
  return `${count} ${count === 1 ? 'source' : 'sources'}`;
}

function presentGrounded(
  reply: PriestAnswerResponse,
  citations: CitationPresentation[],
): AnswerPresentation {
  const points = presentPoints(reply, citations);
  const quotes = presentQuotes(reply);
  if (!points && !quotes) {
    return presentDeclined(reply, NOT_COVERED_NOTICE, 'The Guide could not find this in the library.');
  }
  const reflection = reply.reflection?.trim();
  const blocks: DisplayBlock[] = [
    ...(points ? [points] : []),
    ...(quotes ? [quotes] : []),
    ...(reflection
      ? [{ type: 'reflection' as const, heading: REFLECTION_HEADING, text: reflection }]
      : []),
    ...(reply.notice?.trim() ? [noticeBlock(reply, '')] : []),
    DISCLAIMER_BLOCK,
  ];
  return {
    kind: reply.kind,
    blocks,
    citations,
    announcement: citations.length ? `Answer with ${sourceCount(citations.length)}` : 'Answer',
  };
}

/** Map one API response to the blocks, citations and announcement the screen draws. */
export function presentAnswer(reply: PriestAnswerResponse): AnswerPresentation {
  if (reply.kind === 'crisis') return presentCrisis(reply);
  const citations = reply.citations.map(presentCitation);
  switch (reply.kind) {
    case 'not_covered':
      return presentDeclined(reply, NOT_COVERED_NOTICE, 'The Guide could not find this in the library.');
    case 'deferral':
      return presentDeclined(reply, DEFERRAL_NOTICE, 'The Guide suggests other support for this.');
    case 'library_excerpts':
      return presentExcerpts(reply, citations);
    default:
      return presentGrounded(reply, citations);
  }
}

// ── Failures ────────────────────────────────────────────────────────────

export interface ErrorPresentation {
  message: string;
  /** Whether trying the same question again could work. */
  retryable: boolean;
  /** Whether the screen should offer a way back instead of a retry. */
  offersBack: boolean;
}

const RATE_LIMIT_PREFIX = "Let's pause for a moment.";

function rateLimitMessage(seconds: number | null): string {
  if (seconds === null) return `${RATE_LIMIT_PREFIX} Please try again shortly.`;
  return `${RATE_LIMIT_PREFIX} Try again in ${Math.max(1, Math.ceil(seconds))} s.`;
}

const STATIC_ERRORS: Record<
  Exclude<PriestErrorInfo['code'], 'rate_limited'>,
  ErrorPresentation
> = {
  offline: {
    message: "You're offline. Your question is still here.",
    retryable: true,
    offersBack: false,
  },
  disabled: {
    message: 'The Guide is resting right now. Please come back later.',
    retryable: false,
    offersBack: true,
  },
  busy: {
    message: 'The Guide is busy right now. Please try again in a moment.',
    retryable: true,
    offersBack: false,
  },
  timeout: {
    message: 'That took longer than expected. Please try again.',
    retryable: true,
    offersBack: false,
  },
  unavailable: {
    message: "The study library isn't available right now. Please try again later.",
    retryable: true,
    offersBack: false,
  },
  validation: {
    message: "That question couldn't be sent. Please check it and try again.",
    retryable: true,
    offersBack: false,
  },
  cancelled: {
    message: 'Stopped. Your question is still here.',
    retryable: true,
    offersBack: false,
  },
  unexpected: {
    message: 'Something went wrong. Please try again.',
    retryable: true,
    offersBack: false,
  },
};

/** Fixed user-facing copy for a failed request. The question text is never part of it. */
export function presentError(info: PriestErrorInfo): ErrorPresentation {
  if (info.code === 'rate_limited') {
    return {
      message: rateLimitMessage(info.retryAfterSeconds),
      retryable: true,
      offersBack: false,
    };
  }
  return STATIC_ERRORS[info.code];
}

// ── Waiting ─────────────────────────────────────────────────────────────

/** How long the first status line shows. A client-side timer, not server progress. */
export const STAGE_SWITCH_MS = 2000;

export type PendingStage = 'searching' | 'reflecting';

export function pendingStageAt(elapsedMs: number): PendingStage {
  return elapsedMs >= STAGE_SWITCH_MS ? 'reflecting' : 'searching';
}

export function pendingStageText(stage: PendingStage): string {
  return stage === 'searching' ? 'Searching the library…' : 'Reflecting…';
}
