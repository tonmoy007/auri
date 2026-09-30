# Implementation plan: durable recording jobs and responsible AI recommendations

## Baseline and constraints

- Preserve the existing anonymous device-token model and the current
  confession submission flow.
- Do not add a broker or cloud AI provider solely for queuing. Use Postgres
  leasing and a Compose worker so work survives API restarts.
- Treat the original recording, draft transcript, and masked output as
  sensitive transient artifacts. Do not log their contents.
- Keep current short recordings functional throughout the rollout; switch the
  mobile client only after the job APIs are covered by integration tests.

## Phase A — Define the durable processing boundary

### A1. Job model and migration

Create a `recording_jobs` model and Alembic migration with: random UUID,
device-token hash, idempotency key, voice mask, source/result object keys,
source bytes, duration, public status, attempt count, lease owner/expiry,
failure code, draft transcript metadata, timestamps, and expiry timestamps.

Add unique `(device_token_hash, idempotency_key)` and worker/expiry indexes.
Keep private storage keys out of public response schemas. Define enumerations
for public states and non-sensitive failure codes.

### A2. Settings and retention policy

Add explicit settings for upload byte limit, maximum duration, worker
concurrency, lease interval, processing timeout, source/result TTLs, and
minimum cohort size for HR insights. Add environment-example placeholders and
document production spool encryption requirements. Reuse the existing
retention/purge service pattern for a job-artifact purge command.

### A3. Contract tests first

Write API tests for idempotent creation, streamed-size rejection, owner
isolation, state response shape, cancellation, binary-result authorization,
expiry, and no transcript/audio text in logs or public errors. Test every case
with function-scoped database state.

## Phase B — API ingestion and recovery-safe worker

### B1. Streaming upload endpoint

Implement `POST /api/v1/recording-jobs`. Validate headers and multipart fields
at entry, stream chunks to a random private source path while enforcing the
limit, create or return the idempotent job inside a transaction, and delete a
partial file on every error path. Keep rate limiting on accepted jobs, not on
polls.

### B2. Job read, result, and deletion endpoints

Implement owner-scoped `GET /recording-jobs/{id}`, `GET .../masked-audio`, and
`DELETE .../{id}`. The result endpoint streams compact audio with an accurate
content type and never serializes it as base64. Return actionable typed error
codes (`network_retry`, `server_busy`, `unsupported_audio`, `processing_failed`,
`expired`) rather than opaque error text.

### B3. Worker service

Add `backend/app/workers/recording_processor.py` and a `stt-worker` Compose
service. Claim jobs with `FOR UPDATE SKIP LOCKED`, renew a lease while the
blocking processor runs, and requeue safely only abandoned leases. Load
faster-whisper once per worker process. Run masking/transcoding and
transcription in deterministic stages, check cancellation between stages, and
clean source files in `finally`. Do not automatically retry known model/content
failures; retry only interrupted leased work.

### B4. Worker verification

Add worker tests for claim exclusivity, lease recovery, source cleanup,
cancellation, failed masking/transcription, expiration, and singleton model
ownership. Run a real Compose smoke test with a short fixture and a measured
five-minute speech fixture; record observed processing distribution before
publishing an estimate.

## Phase C — Mobile resilient processing flow

### C1. Pure workflow state and persistence

Create a typed recording-job client and a pure state reducer. Persist only the
job ID, local source URI while upload is incomplete, and minimal UI state in
secure/local device storage; do not persist the raw transcript outside the
server job or existing review flow. Unit-test transition, retry, cancel, and
resume behavior with Vitest without adding a UI-test dependency.

### C2. Processing route

Replace the booth's `Promise.all(transcribe, mask)` path with upload then route
to a dedicated Processing screen. Render real upload bytes and stage-based
server status, adaptive polling with cleanup, screen-reader labels, minimum
44dp actions, and controls for background, cancel/delete, and retry.

### C3. Review integration

Download masked audio to the cache as a binary file only after `ready`; pass a
small job reference into Review rather than a giant route parameter. Present
the transcript as an editable AI draft and show a precise unavailable/retry
state. Preserve the existing audio-session recovery behavior and test playback
after recording.

### C4. Mobile acceptance test matrix

Manually verify 15-second, 2-minute, and 5-minute real speech on the emulator
and one physical device where available. Cover network loss mid-upload,
background/relaunch during every stage, cancel, duplicate tap/retry, failed
worker, expired result, downloaded masked playback, and no original-audio
fallback when masking is unavailable.

## Phase D — Personal AI guidance

### D1. Safe contract and prompt boundary

Add a versioned prompt in `backend/app/llm/prompts/` and a typed endpoint that
accepts only the user-reviewed draft. It returns a short, optional reflection
or next-step suggestion with a disclaimer. Apply de-identification before the
LLM call, request tracing, configured fallback, timeout, and no storage of the
guidance unless the user deliberately submits it as part of a future feature.

### D2. Review UI

Add a dismissible "Optional AI reflection" card after the editable transcript,
with loading/failure states that never block submission. Do not provide medical,
legal, disciplinary, or certainty claims. Add focused unit/API tests for
validation, de-identification order, fallback, and dismissal.

## Phase E — Aggregate HR recommendations

### E1. Privacy-preserving insight dataset

Define a query/service that draws only from de-identified, submitted,
retention-eligible confessions. Apply a configurable minimum cohort threshold,
windowing, category aggregation, and suppression of small groups before any
LLM call. Exclude raw transcript, audio, device token, and individual timing
from both prompt and API response.

### E2. Recommendation generation and audit trail

Add a versioned aggregate-insights prompt and typed response containing themes,
evidence counts, coverage, confidence caveats, and suggested actions. Cache a
short-lived aggregate snapshot only if it contains no individual content. Add
audited acknowledge/dismiss/follow-up actions, with role gating for HR/admin.

### E3. Dashboard UX

Add an Insights recommendation panel that states its aggregate window and
minimum cohort, renders an honest insufficient-data state, makes AI advice
visibly advisory, and never links to individual content. Include filters that
cannot reduce a cohort below the threshold. Add dashboard unit/build checks and
backend tests proving sensitive fields are absent.

## Phase F — Observability, rollout, and review

### F1. Safe operational telemetry

Emit structured, content-free metrics: queue depth, stage duration, duration
bucket, successful/failed/cancelled count, retry count, lease recovery, and
purge count. Add request/job correlation IDs without transcript/audio values.

### F2. Controlled rollout

Ship backend/job APIs and worker first behind a mobile-configurable feature
flag. Enable the new mobile flow for development, then test devices. Keep the
old endpoint operational only for a defined deprecation window and remove it
after measured success.

### F3. Final verification and documentation

Run Python lint/type/tests, mobile typecheck/related Vitest tests, Docker build,
and the long-recording acceptance matrix. Independently review the privacy
boundary and job cleanup. Update the project plan/tracking only after each
atomic implementation task is committed and verified.

## Delivery order

1. A1–A3
2. B1–B4
3. C1–C4
4. D1–D2
5. E1–E3
6. F1–F3

Each numbered subtask should be a separate commit and reviewable pull request
slice. No phase may claim completion without its cleanup/privacy tests and the
corresponding real-device or Compose evidence.
