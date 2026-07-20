# Privacy & Anonymity Review

**Date:** 2026-07-20
**Scope:** End-to-end trace of a confession's data — LLM prompts, database rows, log lines, and Telegram delivery payloads — checking for PII leakage against the plan's Data Privacy Design (`.hermes/plans/2026-07-16_143000-auri-plan.md`, "Data Privacy Design" section).

## Method

Static trace, not a runtime scan: followed `body.transcript` (the raw, un-redacted confession text) from `POST /api/v1/confessions` through every function it's passed to, and separately grepped every `logger.*` call across `backend/app` and `bot` for anything that might carry transcript content.

## Findings

### 1. FIXED — `bot/main.py` `error_handler` logged the full raw `Update` object

```python
# before
logger.error("Update %s caused error %s", update, context.error)
```

`python-telegram-bot`'s `Update.__str__` includes the full message text. Any exception during update processing — a bug, a transient API failure, anything — would have written the complete message content (which could include a forwarded confession, still potentially carrying PII the LLM de-identification pass missed) to application logs at ERROR level. Log aggregators typically have broader access and longer retention than the primary database, so this was a real leak path even though the DB itself was clean.

**Fix:** log only `update.update_id` (a non-sensitive sequence number) plus the exception. Regression test added: `bot/tests/test_handlers.py::test_error_handler_never_logs_raw_update_content`.

### 2. DOCUMENTED, NOT FIXED — `moderate()` receives the RAW transcript, not the de-identified one

`backend/app/api/v1/confessions.py`:

```python
category = _safe_categorize(llm_service, deidentified_transcript)
ai_summary = _safe_summarize(llm_service, deidentified_transcript)
is_flagged = _safe_moderate(llm_service, body.transcript)  # ← raw, not deidentified_transcript
```

This is **deliberate**, not an oversight — see the 2026-07-18 fix (`app/api/v1/confessions.py` git history) for the incident that caused it: de-identification itself can fail (a smaller/weaker LLM refusing the redaction prompt on self-harm content was observed live, corrupting its own output), and if `moderate()` reads that corrupted output instead of the original text, it can silently miss the exact content it exists to catch. Moderating the raw transcript makes the safety check's reliability independent of a separate LLM call succeeding cleanly.

**The tradeoff this accepts:** the raw, un-redacted transcript (which may contain names, emails, or other PII the user spoke) is sent to the external LLM provider (OpenAI/Claude) for the moderation check, whereas every other LLM call (`categorize`, `summarize`) only ever sees the de-identified version. This is a real, non-zero exposure to a third party — bounded by that provider's own data-handling terms, not Auri's. It is never stored: the raw transcript is not persisted anywhere, only `deidentified_transcript` is written to the `confessions.transcript` column.

**Recommendation, not implemented here:** if this tradeoff becomes unacceptable, the fix is not to revert to moderating de-identified text (that reintroduces the original bug) — it's to make `deidentify()` itself fail loudly instead of silently degrading, so `moderate()` can wait for a *verified-clean* de-identified version instead of choosing between "possibly corrupted" and "definitely raw."

### 3. CLEAN — Database rows

- `confessions.transcript` stores `deidentified_transcript` only — the raw transcript never reaches the database (confirmed: no code path writes `body.transcript` directly to a `Confession` row).
- `confessions.device_token_hash` is a SHA-256 hash of the client's local device token, not the token itself, and not any real-world identifier — it cannot be reversed to identify a device or person.
- `category` / `ai_summary` are both derived from the de-identified transcript.

### 4. CLEAN — Telegram delivery (moderation queue)

`bot/moderation_handlers.py`'s `_format_queue_item()` sends `category`, `ai_summary`, and `transcript` to the moderator's chat — all three sourced from the `GET /api/v1/moderation/queue` response, which returns `ConfessionResponse` built from the DB row (de-identified transcript, per finding 3). The moderator never sees raw content.

*(The recipient-facing delivery path — actually forwarding a confession's content to a real recipient, not the moderator — is not yet built; `bot/main.py`'s `handle_confession_message` is still an acknowledgement stub per plan task 4.5. Re-run this section of the review once that's implemented.)*

### 5. CLEAN — Other logging

Every other `logger.*` call across `backend/app` and `bot` that touches an exception logs `str(exc)` (the exception's own message) or a UUID/status/count — none pass transcript, summary, or raw request-body content as a log argument.

## Summary

| # | Finding | Status |
|---|---|---|
| 1 | Bot error handler logged full `Update` (message content) on any failure | **Fixed** |
| 2 | `moderate()` sends raw (non-de-identified) transcript to the external LLM | Documented, deliberate tradeoff — not fixed |
| 3 | DB rows never store raw transcript or reversible identifiers | Clean |
| 4 | Moderation-queue Telegram delivery only carries de-identified content | Clean |
| 5 | No other log line carries transcript/summary content | Clean |

Not audited here — call these next when they're built: the real recipient-delivery path (plan 4.5), and the LiveKit real-time pipeline (plan Phase 7) if it ships, since a live audio stream is a different leakage surface than a batch upload.
