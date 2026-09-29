// Auri — History screen presentation logic
// Pure TypeScript with no react-native, expo or theme imports, so vitest can
// run it in a plain node environment (react-native cannot be parsed there).

/** Heading shown above an HR reply. Static: it never names a person or a role. */
export const HR_REPLY_HEADING = 'Reply from HR';

const EDITED_MARKER = 'Edited';
const META_SEPARATOR = ' · ';

/** The reply fields the backend adds to every confessor confession. */
export interface HrReplyFields {
  hr_reply: string | null;
  hr_replied_at: string | null;
  hr_reply_edited_at: string | null;
}

/** Turns an ISO 8601 timestamp into the short date string the UI shows. */
export type DateFormatter = (iso: string) => string;

/**
 * Formats an ISO timestamp as a short date and time, for example "Sep 29, 2:05 PM".
 * `locale` and `timeZone` default to the device settings; tests pin both.
 */
export function formatHistoryDate(iso: string, locale?: string, timeZone?: string): string {
  return new Date(iso).toLocaleDateString(locale, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    ...(timeZone ? { timeZone } : {}),
  });
}

/** Everything the reply card renders, already resolved to plain strings. */
export interface HrReplyPresentation {
  heading: string;
  body: string;
  meta: string;
  accessibilityLabel: string;
}

/**
 * Builds the reply card content for a confession, or null when there is
 * nothing to show (no reply, a blank reply, or a reply without a saved time).
 *
 * The body is the stored reply verbatim: no trimming, truncation or rewriting.
 */
export function presentHrReply(
  fields: HrReplyFields,
  formatDate: DateFormatter,
): HrReplyPresentation | null {
  // Falsy checks (not `=== null`) so a response from a backend that predates
  // the reply fields, where these keys are absent, shows no card instead of crashing.
  if (!fields.hr_reply || fields.hr_reply.trim() === '') {
    return null;
  }
  if (!fields.hr_replied_at) {
    return null;
  }

  const editedSuffix = fields.hr_reply_edited_at
    ? `${META_SEPARATOR}${EDITED_MARKER} ${formatDate(fields.hr_reply_edited_at)}`
    : '';
  const meta = `${formatDate(fields.hr_replied_at)}${editedSuffix}`;
  const body = fields.hr_reply;

  return {
    heading: HR_REPLY_HEADING,
    body,
    meta,
    accessibilityLabel: `${HR_REPLY_HEADING}, ${meta}. ${body}`,
  };
}
