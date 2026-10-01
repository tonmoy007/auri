// Auri — Guide (priest mode) wire types
// Mirrors backend/app/priest/schemas.py field for field. The wire format is
// snake_case, so these types are too; do not rename fields here without the
// backend changing first.

/** What sort of response this is; the app renders each kind differently. */
export type PriestAnswerKind =
  | 'answer'
  | 'not_covered'
  | 'crisis'
  | 'deferral'
  | 'library_excerpts';

/** Every kind the server can return — used to reject a body the app cannot draw. */
export const PRIEST_ANSWER_KINDS: readonly PriestAnswerKind[] = [
  'answer',
  'not_covered',
  'crisis',
  'deferral',
  'library_excerpts',
];

/** The traditions the study library covers, for the optional filter. */
export type TraditionId =
  | 'judaism'
  | 'christianity'
  | 'islam'
  | 'hinduism'
  | 'buddhism'
  | 'jainism'
  | 'sikhism'
  | 'zoroastrianism'
  | 'confucianism'
  | 'daoism'
  | 'egyptian'
  | 'mesopotamian';

/** Display names for {@link TraditionId}, mirroring the backend's TRADITION_LABELS. */
export const TRADITION_LABELS: Readonly<Record<TraditionId, string>> = {
  judaism: 'Judaism',
  christianity: 'Christianity',
  islam: 'Islam',
  hinduism: 'Hinduism',
  buddhism: 'Buddhism',
  jainism: 'Jainism',
  sikhism: 'Sikhism',
  zoroastrianism: 'Zoroastrianism',
  confucianism: 'Confucianism',
  daoism: 'Daoism',
  egyptian: 'Egyptian religion',
  mesopotamian: 'Mesopotamian religion',
};

/** Every tradition id, in picker order. */
export const TRADITION_IDS = Object.keys(TRADITION_LABELS) as TraditionId[];

/** Body of POST /api/v1/priest/ask. The server rejects any other field. */
export interface PriestAskRequest {
  question: string;
  tradition?: TraditionId;
  language: 'en';
}

/** One statement from the library, with the sources it rests on. */
export interface PriestPoint {
  text: string;
  citation_ids: string[];
}

/** A verbatim quotation; the label is built by the server from note metadata. */
export interface PriestQuote {
  text: string;
  citation_id: string;
  label: string;
}

/** A source note the answer rests on. */
export interface PriestCitation {
  id: string;
  note_title: string;
  heading_path: string[];
  note_type: string;
  tradition_labels: string[];
  snippet: string;
}

/** A way to reach help; `dial` is a number the server considers safe for `tel:`. */
export interface CrisisContactOut {
  label: string;
  detail: string;
  dial: string | null;
}

/** What the guide returns for one question. */
export interface PriestAnswerResponse {
  request_id: string;
  kind: PriestAnswerKind;
  points: PriestPoint[];
  quotes: PriestQuote[];
  reflection: string | null;
  citations: PriestCitation[];
  notice: string | null;
  contacts: CrisisContactOut[] | null;
  disclaimer_version: string;
  index_version: string | null;
  prompt_version: string | null;
}

/** One entry of the tradition picker, as the status endpoint sends it. */
export interface TraditionOption {
  id: string;
  label: string;
}

/** Body of GET /api/v1/priest/status. */
export interface PriestStatus {
  enabled: boolean;
  persona_name: string;
  traditions: TraditionOption[];
  disclaimer_version: string;
  max_question_chars: number;
  /** Contacts for the always-visible help block, when the server sends them. */
  crisis_contacts?: CrisisContactOut[] | null;
}

/** Why a Guide request failed, as the client tells failures apart. */
export type PriestErrorCode =
  | 'offline'
  | 'rate_limited'
  | 'disabled'
  | 'busy'
  | 'unavailable'
  | 'timeout'
  | 'validation'
  | 'cancelled'
  | 'unexpected';

/** The part of a Guide failure the presentation layer needs. */
export interface PriestErrorInfo {
  code: PriestErrorCode;
  /** Seconds from the server's Retry-After header; set for `rate_limited` and `busy`. */
  retryAfterSeconds: number | null;
}
