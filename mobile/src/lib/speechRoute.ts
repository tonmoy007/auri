// Auri — which speech-to-text route to call
//
// A confession's audio may, on the server, be retried through a hosted provider when
// local transcription fails. A spoken Guide question must never reach one, so the Guide
// asks for local-only transcription. Kept pure so the rule is testable.

export interface SpeechOptions {
  /** Never retry through a hosted provider. */
  localOnly?: boolean;
}

/** The speech-to-text path for `base`, with the local-only switch when asked for. */
export function speechToTextPath(base: string, options: SpeechOptions = {}): string {
  return options.localOnly ? `${base}?local_only=true` : base;
}
