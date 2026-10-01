# Long-recording processing and AI experience

## Decision

A recording is processed as one durable, short-lived job. The client uploads
the original audio once; the API acknowledges it immediately; a worker masks
the voice and drafts a transcript. The app can safely background, restart, and
resume that job. AI has two distinct, consent-aware roles:

1. **Confessor guidance** — an optional, clearly-labelled personal suggestion
   shown only after the user has reviewed the AI transcript and before they
   submit. It is never required to submit and never routes content elsewhere.
2. **HR insight recommendations** — aggregate-only themes and recommended
   actions, generated from de-identified, retention-eligible confessions. This
   surface must never expose an individual transcript, audio file, job, or
   identity-adjacent metadata.

## Why this is necessary

The present client waits for synchronous `POST /stt` and `/voice/mask` calls in
parallel. A five-minute speech recording was successfully transcribed in about
9 minutes 46 seconds, whereas the mobile upload helper times out after 30
seconds and retries. Masking returns a 33.6 MB base64 JSON response for the
same recording. This makes duplicate CPU work, mobile-memory pressure, false
"100%" progress, and a poor recovery path inevitable. A larger timeout is not
a reliable fix.

## Recording job contract

`POST /api/v1/recording-jobs` accepts multipart audio, `voice_mask`, and an
idempotency key. It streams the input to a private spool rather than reading
the entire body into memory, validates a configured byte/duration limit, and
returns `202` with a job identifier.

The caller proves ownership with the existing device-token hash. The server
enforces a unique `(device_token_hash, idempotency_key)` pair, returning the
original job for an upload retry. Job IDs are random UUIDs and are never enough
to read another device's work.

The status endpoint returns only a stable public state:

`queued`, `masking`, `transcribing`, `ready`, `failed`, `cancelled`, or
`expired`.

It may return stage names, retry guidance, and a duration-derived estimate; it
must not manufacture a percentage for model work. When ready, it returns the
draft transcript and lightweight metadata such as detected language and a
review-needed flag. Masked audio is retrieved through a separate authenticated
binary endpoint (`audio/aac`), not encoded inside JSON. `DELETE` cancels a
queued job and purges artifacts. A running native transcription is cancelled at
the next safe stage boundary; UI copy must state that accurately.

## Worker and storage design

A separate `stt-worker` Compose service shares a dedicated private spool with
the API. Postgres is the durable queue: the worker atomically claims one queued
job using a lease, renews the lease while CPU work runs, and recovers expired
leases after a restart. Concurrency defaults to one CPU-bound job and is
configurable only through server settings.

The worker holds one lazily loaded Whisper model per process, rather than
loading a model per request. It masks and transcodes the audio to a compact
playback format, then transcribes the source with configured VAD/model options.
It records explicit failure codes without recording audio or transcript text in
logs.

Raw input is temporary only. Source audio is deleted after either successful
processing or terminal failure/cancellation. The transcript and masked result
expire after short, separately configured TTLs so a restarted client can finish
reviewing without turning the job table into message storage. A periodic purge
deletes expired rows and orphaned spool files. The implementation must use a
dedicated non-public directory with restrictive permissions; production should
encrypt this spool at rest using a deployment-managed key before launch.

## Mobile experience

Stopping a recording navigates to a dedicated Processing screen rather than
leaving the booth frozen. Upload progress is real; server work is represented
by honest stages:

1. Uploading securely
2. Protecting your voice
3. AI is drafting your transcript
4. Ready to review

The screen explains that long recordings take longer, shows a duration-based
estimate only when measured, and lets the user continue in the background,
resume after relaunch, retry a recoverable error, or discard the job. A local
persisted job reference lets the app reattach without re-uploading. No base64
audio is kept in React state; masked audio downloads into the app cache only
once the job is ready.

Review prominently labels the transcript as an **AI draft — review before
sending** and keeps editing accessible. Playback is enabled only after the
masked file has downloaded. The personal guidance card is optional, explains
that it is AI-generated, and is computed only after the user has a transcript
to review. It must not claim clinical, legal, or HR authority and must have a
clear dismiss control.

## HR insight recommendations

The dashboard receives a new aggregate Insights recommendation surface. It
uses only de-identified, retention-eligible submitted confessions and minimum
cohort thresholds. It shows trend/theme evidence, confidence/coverage, the
time window, and suggested organisational actions. It must not include raw
transcripts, audio, individual submission timestamps, device tokens, or
per-person recommendations. Low-volume cohorts show an explicit "not enough
anonymous data" state instead of generating an inference.

Recommendations are advisory drafts, not automated HR decisions. An HR user
can acknowledge, dismiss, or mark an action for follow-up; those writes are
audited. AI failure degrades to existing non-AI aggregates and never blocks a
confession submission.

## Acceptance conditions

- A five-minute recording creates exactly one job even after client retries.
- The app remains usable during server processing and resumes a job after a
  background/relaunch event.
- No route sends large masked WAV/base64 payloads through JSON or React state.
- A job's raw source disappears on terminal outcome and all residual artifacts
  are purged at TTL expiry.
- The user can review/edit an AI draft before submitting; guidance is optional.
- HR recommendations are thresholded, aggregate-only, audited, and cannot
  reveal an individual confession.
