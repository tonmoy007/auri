# ADR: recording limit and background transcription

**Status:** accepted, 2026-10-01 (plan tasks 16.1, 16.3, 16.5)

## Context

The app records up to five minutes (`MAX_RECORDING_DURATION_MS`). Whisper
`base` on the reference stack runs 0.6x to 2.6x slower than real time, so a
five-minute recording took about ten minutes to transcribe, all of it inside the
upload request and, until 16.8, on the server's event loop. The server checked
only the file size.

## Decision

Owner decision 2026-10-01: **keep the five-minute limit and move transcription
to the background** rather than lower the limit.

- `POST /api/v1/stt?mode=job` returns a job id at once; the app polls
  `GET /api/v1/stt/jobs/{id}` (16.1, app side 16.2).
- The server enforces the same limit: `MAX_RECORDING_SECONDS` (default 300) is
  checked with `ffprobe` before any transcription or masking, with a one-second
  margin for encoder padding, and a longer recording gets a typed 413 (16.5).

## Consequences

- The limit lives in two places, the app's `MAX_RECORDING_DURATION_MS` and the
  server's `MAX_RECORDING_SECONDS`, kept equal by hand and named in each other's
  comments. The app cannot read a server setting before it records, and the
  server must not trust a value the app sends.
- When `ffprobe` is missing or cannot read a file, only the size cap applies.
- A five-minute confession still means minutes of waiting; 16.2 replaces the
  frozen screen with a real waiting state the user can leave and come back to.
