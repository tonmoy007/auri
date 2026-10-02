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

**The tradeoff this accepts:** the raw, un-redacted transcript (which may contain names, emails, or other PII the user spoke) may be sent, when the local model cannot answer, to an external LLM provider (Gemini or OpenAI; see finding 13) for the moderation check, whereas every other LLM call (`categorize`, `summarize`) only ever sees the de-identified version. This is a real, non-zero exposure to a third party — bounded by that provider's own data-handling terms, not Auri's. It is never stored: the raw transcript is not persisted anywhere, only `deidentified_transcript` is written to the `confessions.transcript` column.

**Recommendation, not implemented here:** if this tradeoff becomes unacceptable, the fix is not to revert to moderating de-identified text (that reintroduces the original bug) — it's to make `deidentify()` itself fail loudly instead of silently degrading, so `moderate()` can wait for a *verified-clean* de-identified version instead of choosing between "possibly corrupted" and "definitely raw."

### 3. CLEAN — Database rows

- `confessions.transcript` stores `deidentified_transcript` only — the raw transcript never reaches the database (confirmed: no code path writes `body.transcript` directly to a `Confession` row).
- `confessions.device_token_hash` is a SHA-256 hash of the client's local device token, not the token itself, and not any real-world identifier — it is not a name or any real-world identifier, but it is a stable per-phone code: it ties every confession from one phone together, and, while `DEVICE_HASH_PEPPER` is unset or for rows whose phone has not yet returned, it works as a credential for that phone's confessions (see finding 9). With the secret set the server stores `v2:` plus an HMAC-SHA256 of it instead.
- `category` / `ai_summary` are both derived from the de-identified transcript.

### 4. CLEAN — Telegram delivery (moderation queue)

`bot/moderation_handlers.py`'s `_format_queue_item()` sends `category`, `ai_summary`, and `transcript` to the moderator's chat — all three sourced from the `GET /api/v1/moderation/queue` response, which returns `ConfessionResponse` built from the DB row (de-identified transcript, per finding 3). The moderator never sees raw content.

*(The recipient-facing delivery path is now built; this section was re-run for it in the addendum below.)*

### 5. CLEAN — Other logging

Every other `logger.*` call across `backend/app` and `bot` that touches an exception logs `str(exc)` (the exception's own message) or a UUID/status/count — none pass transcript, summary, or raw request-body content as a log argument.

## Summary

| # | Finding | Status |
|---|---|---|
| 1 | Bot error handler logged full `Update` (message content) on any failure | **Fixed** |
| 2 | `moderate()` sends raw (non-de-identified) transcript to the external LLM | Documented, deliberate tradeoff — not fixed |
| 3 | DB rows never store raw transcript or reversible identifiers | Clean |
| 4 | Moderation-queue Telegram delivery only carries de-identified content | Clean — but see the addendum: "de-identified" means recognised details replaced, not anonymous |
| 5 | No other log line carries transcript/summary content | Clean |

Not audited here — the LiveKit real-time pipeline (plan Phase 7) if it ships, since a live audio stream is a different leakage surface than a batch upload. The recipient-delivery path (plan 4.5) is built now and is covered in the addendum below.

---

# Addendum — the HR access surface (Phase 11)

**Date:** 2026-09-30
**Why this exists:** the review above describes a system where nobody but the bot ever reads a confession. Since Phase 11 that is no longer true: staff sign in, read summaries, sometimes read transcripts, see aggregates, write replies, and read a Privacy panel. This addendum records who can now see what, the controls that limit it, and what an independent fact-check of the Privacy panel found when each of its sentences was traced to the code. The panel is built to say no more than this document does.

## Who can see what

| Who | What they can see | Recorded? |
|---|---|---|
| Confessor's own phone | Their confessions (including the stored transcript), HR's replies, and, briefly, a copy of the recording: the masked one is deleted when a confession is sent or deleted, when they record again, or at the next app start; if masking fails the unmasked recording is kept until then (finding 10) | — |
| Moderator | On the Queue tab, the **full transcript** of every item held for review, with no reason required | Listing is audited at the `raw` tier when done from a dashboard session; not when done from Telegram with the bot's key |
| HR | Summary, category and mood label of each confession (individually, with department and exact send time); aggregates on Insights and Themes; delivery metadata; the Queue; replies. The **full transcript** only of items held for review (crisis items stay readable after release), with a written reason of at least 12 characters | Yes, for dashboard sessions: account, time, tier, reason. The row is committed before the response is sent, so a failure to record it fails the request and releases nothing (finding 5) |
| Admin | All of the above, the audit trail, staff accounts, and the live configuration | Config writes are audited by key and action, never the value (commit d4db828); before that they were not |
| Department Telegram chats | Category, summary and the first 1,000 characters of the transcript of every forwarded confession; moderators' chat gets 500 characters | No, and Telegram keeps the messages after this system deletes the confession |
| Anyone with database access | Every stored transcript, which confessions came from the same phone, and, unless `DEVICE_HASH_PEPPER` is set (and the row already upgraded), the ability to read, forward or withdraw them with that phone's stored code | No |

## Controls in place

- **Tiered reads, enforced in the query.** The summary tier never selects the transcript column (11.5). A raw read is a `POST` with a stated reason, refused for ineligible items with no audit row and no content.
- **k-anonymity suppression, server-side.** A figure covering fewer than `ANALYTICS_MIN_COHORT` (floored at 2) confessions is withheld before serialisation (11.6). Themes are stricter: a theme below the cohort is withheld *with its label*, and a sentiment share is withheld unless both the negative count and its complement are zero or at least the cohort, because `share × total` returns the exact count (11.13).
- **Append-only audit trail** of staff reads and mutations (11.4), enforced at application level; there is no database trigger or revoked permission.
- **Retention.** Forwarded and withdrawn confessions are removed at the first job run after `RETENTION_HOURS` of no change. A replied forwarded confession is emptied to a **reply-only shell**: the reply, its timestamps and the phone's hash remain for `REPLY_RETENTION_DAYS` after the reply (owner decision, 11.16). Shells are invisible to every staff and delivery query. Each run is logged with the windows it enforced (11.14), and the Privacy panel warns when a run is overdue against `RETENTION_EXPECTED_RUN_HOURS`.
- **Themes stay local by default.** Grouping runs on local Ollama over summaries only; a remote OpenAI-compatible model can be configured (11.18) but plain `http` to a public address is refused unless explicitly allowed, and the panel reports where summaries actually go.
- **Credentials and content kept out of logs** (11.19): the Gemini key is sent in a header, model replies are logged by length, SQL echo is opt-in, and Sentry does not attach request bodies.

## Findings from the fact-check of the Privacy panel

The first draft of the panel made eight guarantees; six overstated what the system does. It was rewritten before it shipped. The underlying gaps:

| # | Finding | Status |
|---|---|---|
| 1 | The Gemini key was sent as a `?key=` URL parameter and printed by the failure log | **Fixed** (11.19). Rotate the key if those logs were ever shipped |
| 2 | `moderate()` and `classify_sentiment()` logged model output with `%r`; moderation reads the *original* transcript | **Fixed** (11.19) |
| 3 | SQL echo was on whenever `ENVIRONMENT=development` (the default), logging every INSERT's transcript, summary and reply | **Fixed** (11.19) |
| 4 | Sentry attaches JSON request bodies regardless of the PII flag | **Fixed** (11.19) |
| 5 | The audit row was committed *after* the response was sent, so a failing commit still returned the content | **Fixed** (11.22). Trade-off: while the database cannot accept writes, audited dashboard reads fail too, including the moderation Queue |
| 6 | Moderation from Telegram (the bot's shared key) and readers of the department chats are not audited | **Partly fixed** (11.20): a decision made from Telegram with the bot's shared key is now an audit row attributed to `telegram-bot` (the key names no person, so nobody is stamped as reviewer). **Decided, not changed:** the bot's queue polls are not recorded (every few seconds; a row each would bury the trail), and who reads the Telegram chats cannot be recorded by this system |
| 7 | The Queue tab shows every held transcript without a stated reason, contrary to the "reason required" rule for HR | **Decided** (11.20): the Queue stays reason-free. It is a safety workflow, and a crisis item cannot wait for a typed reason. Listing it from a dashboard session is audited at the `raw` tier and the Privacy panel says moderators and HR see full held transcripts. The 'reason required' rule therefore applies to HR opening a transcript outside the Queue, and this is the documented exception |
| 8 | Telegram posts up to 1,000 transcript characters per forwarded confession, and the message footer says "The sender's identity is never stored or shared" | **Text fixed** (11.21): the footer now says only that no sender name or device details are attached; the bot's welcome, `/confess`, `/forward` and plain-message replies no longer say identity is "never stored", that the recipient cannot know the sender, or that anything was delivered from that chat (the last two were untrue as well as overclaimed). The same sweep corrected the booth's landing and settings text, the anonymity and forward screens, the Replies, Themes and Audit tab descriptions and the README. **Made adjustable** (11.20): `DELIVERY_TRANSCRIPT_CHARS` (default 1,000, at most 2,500 so Telegram accepts the message; 0 posts the category and summary only) is applied by the backend before the bot sees the transcript, and the Privacy panel states the live value. Default unchanged. **Not changed, tracked:** the "Fully blind" and "Kept private" labels, which describe delivery without a recipient, not that the confession goes nowhere (it is stored and readable by staff), and the mobile history label "Removed after 24h", which is a fixed string rather than the live setting |
| 9 | The stored `device_token_hash` works as a bearer credential; `anonymous_users` is never purged | **Fixed** (11.21, 11.24). `anonymous_users` is purged once its rate-limit window passes. The code is now stored as `v2:` plus an HMAC-SHA256 under `DEVICE_HASH_PEPPER`, so a database copy alone cannot be presented as a phone; a value copied out of the database is never accepted as an as-sent code. **Conditions:** the secret must be set (the Privacy panel says when it is not); rows from before it was set stay as sent until their phone next returns or `python -m app.services.device_identity` is run; losing or changing the secret strands every phone's history and unread HR replies (there is no rotation); turning it off again makes the stored `v2:` values usable as codes, because with no secret the server compares the value as sent. The secret is read from `.env` and is not protected beyond that (prototype) |
| 10 | The mobile app writes masked recordings to its cache and never deletes any recording | **Fixed in code** (11.21), not yet checked on a device: the unmasked recording is deleted once masking succeeds, both copies on submit, confirmed delete, re-recording or leaving the booth, and anything left behind at the next app start. Deletion only touches files this app made in its own cache |
| 11 | A failed SoX voice mask leaves its output in `data/modulated` | **Fixed** (11.21): the output is deleted on every failure path (non-zero exit, timeout, SoX missing) |
| 12 | When local speech-to-text fails or hears nothing, the raw audio is sent to OpenAI (`whisper-1`) | **Disclosed**, not changed; conditional on an OpenAI key being configured |
| 13 | Every LLM step (de-identification, safety check, summary, category, mood label, the confessor's reply, the preview) uses the `auto` chain and can fall back to Gemini or OpenAI. Earlier in this document only `moderate()` was noted | **Disclosed**, not changed |
| 14 | The stored transcript is the confessor's own words with *recognised* details replaced (`pii_stripped` is set even if only the regex ran); it is de-identified, not anonymous | **Disclosed**; the panel says "recognised" |
| 15 | Pending and flagged confessions are never removed, and a reply to a pending confession is kept with it | **Fixed** (plan 14.10, owner decision 2026-10-01): every confession follows `RETENTION_HOURS` after its last change; a replied one becomes a reply-only shell like a forwarded one. A held item can now leave before review, so each run records flagged items removed and crisis items removed unacknowledged, shown (suppressed below the cohort) in the Privacy panel |
| 16 | HR lists show individual confessions with department and exact time; Replies, Delivery and Queue print exact counts | **Disclosed**; suppression applies to Insights and Themes only |
| 17 | Insights accepts arbitrary `since`/`until` windows, so two overlapping requests can be differenced to recover a suppressed bucket | **Fixed** (plan 14.1, owner decision 2026-10-01): Insights takes a calendar month and reports its four month-aligned weeks, which nest in months, with no month total beside them. A week is shown only once frozen (ended, every confession folded into the count-only rollups of 14.12), so its figures never change. Inside a week a lone withheld part takes the smallest shown part with it, and a withheld total withholds every part. Whole-day windows alone would not have closed it |
| 18 | The reply-only shell keeps the device hash for up to `REPLY_RETENTION_DAYS` after the reply | **Decision recorded** (11.16) |

## What the Privacy panel does about it

`GET /api/v1/privacy/overview` builds its guarantees and its *limits* from the live configuration — including provider keys and the model address set in the Config tab — so a sentence cannot outlive the setting that made it true. Findings 8, 10, 12–16 above appear on it as limits, in the same plain language as the guarantees. When tasks 11.20 and 11.21 land, the corresponding limits must be edited or removed in `backend/app/services/privacy_overview.py`; the tests pin the wording to the configuration but cannot know that the code has changed underneath them.

## Addendum: priest mode (the "Guide"), 2026-10-01

An AI that answers questions from a study library. Design: `docs/adr/priest-mode.md`. It is off by default.

### Data flow

| Step | What happens | Kept? |
|---|---|---|
| Phone to API | The question (at most 1,000 characters), an optional tradition (the choice is saved on the phone but is sent with each question), the language, and the device header. No confession id or text is accepted (the request forbids extra fields). | Nothing is stored. |
| API | Recognised details are removed by pattern matching only (no model step, to save seconds). The question is checked against fixed lexicons (on the original and the cleaned text). Crisis, deferral and non-Latin-script questions are answered with fixed text at this point and are not metered by the rate limiter. | In memory only: a per-device request count keyed by an HMAC with a per-process salt, pruned lazily (see finding 23). |
| API to moderation Ollama | For a question that passed the lexicons, the **original question as typed, before de-identification**, sent to the Ollama at `OLLAMA_BASE_URL` (environment), with `OLLAMA_MODEL`. Not sent for questions already routed to crisis, deferral or the English-only notice. Two-thread pool, HTTP call capped at 5 s. | That Ollama's operator can see it while it is handled. |
| API to embedding Ollama | The question with recognised details removed, to embed it for retrieval, at the same environment address. | That Ollama's operator can see it while it is handled. |
| API to model server | The question with recognised details removed, the tradition label if the person chose one (it is written into the prompt), the retrieved notes, and a random request id. No device code, no address, no confession data. The address is environment-only; well-known hosted providers and `-cloud` models are refused (the provider list is not exhaustive, and the environment-only address is the safeguard). | The model server's operator can see it while it is answered. |
| Logs and metrics | Request id, outcome kind, stage timings, index and prompt versions, validator codes, source count. Never the question, answer, snippets, tradition or device code. Metric labels come from fixed sets; `/metrics` folds crisis and deferral into one `fixed_reply` label. Separately, each model server's access log records the time and client address of a request, and the request time of a question appears in Auri's API access log. | Metadata only in Auri's own logs; the model servers' logging is unverified. |
| Staff | No endpoint returns Guide content. Admins see index status, reports and usage counts. Counts below the cohort are withheld, latency is withheld until the total reaches the cohort, and an `other` row is always listed. Differencing the counts over time can still show when a count crosses the cohort (admin-only; accepted for the prototype). | |

### Findings

| # | Finding | Status |
|---|---|---|
| 19 | Questions travel to the model server, at present over plain HTTP on a public address with an API key; its operator can read them while processing, and its logging is unverified. The same holds for the fallback Ollama and for the moderation and embedding Ollama (moderation reads the question as typed) | **Open, accepted for the prototype.** Harden before any pilot: `docs/runbooks/priest-llm-server.md`. The Privacy panel states the live route and says when it is unencrypted |
| 20 | De-identification of a question is pattern matching only; names and rare details can reach the model server | **Decided** (D11), documented |
| 21 | A crisis question gets a fixed message with contacts, and **nobody at the company is told**, because nothing is kept. This differs from confessions, which are held for a named person | **Decided** (D10). The wording was written for review and needs clinical and HR sign-off |
| 22 | **Voice input** reuses the speech-to-text route. If local transcription fails and `OPENAI_API_KEY` is set, the audio of the question is sent to OpenAI, which breaks the rule that Guide text never reaches a third party | **Fixed** (13.19): the speech route takes `local_only`, which switches the OpenAI fallback off, and the Guide's microphone always sets it; a test with a key configured proves no connection is attempted. A failed local transcription now gives an error and the person can type instead |
| 23 | The rate limiter is in memory and per process, and can be flooded to evict a real device's window. The Guide's limiter keys on an HMAC with a per-process salt, so the raw device code is not kept; idle entries are dropped lazily, on the next request to the limiter, so an entry can outlive 24 hours when no one asks. The identity is the device header the client sends, so a client can vary it. The speech-to-text limiter that voice questions also pass through (`app/api/v1/stt.py`) keeps the raw device hashes of every caller in a dict for the life of the process and never prunes it | Accepted (ADR); IP limiting not added |
| 24 | Religious belief is special-category personal data. The tradition choice is saved on the phone, but it is sent with every question as a per-request field and is written into the prompt to the model server (as a label), so the model server's operator can see it with the question. It is not stored or logged by Auri | Designed in (not stored or logged, pinned by a test); the exposure to the model server is disclosed |
| 25 | The study vault looks bulk-authored, so its quotations and verse references are unverified; a quote check proves only that it is in the vault | Disclosed in the interface ("quoted in the note", never "scripture says") |
| 26 | Copyright: the vault's own prose is low risk, its quotations' translations are unnamed, and the sibling `scriptures/` folder (non-commercial and unverified licences) is excluded | **Deferred** by decision |
| 27 | The crisis, medical, legal, abuse and judge-a-person lexicons and their deferral texts are first drafts (crisis lexicon now version 4, abuse version 3, after the independent review and its verification pass below) | **Open:** need clinical and HR review before a pilot |

### Independent review (Guide), findings and status

Six reviewers read the Guide after the first implementation. The numbers below are this document's rows, not the reviewers' identifiers; findings are grouped by area. The code fixes are in commits 22ce3d3 (API safety, privacy, grounding), 4d600a0 (mobile) and d4db828 (knowledge pipeline, admin API). "Fixed" means the code changed and tests were added; none of it has been tested against real users, and the wording of every fixed reply still needs the clinical and HR review listed as open.

| # | Group | Status |
|---|---|---|
| 28 | Crisis path, API: the rate limiter refused crisis, deferral and unsupported-script replies; false alarms (prescribed prayers, Sue, "fire me up", scriptural figures in the judge rule); missed phrasings and abuse disclosures; obfuscated words | **Fixed** (22ce3d3): those replies skip the limiter; crisis lexicon v3, abuse v2; false alarms removed; broken-up words, look-alike Cyrillic and Greek letters and a leading `$` are read |
| 29 | Non-Latin scripts could be answered or slip past the English lexicon | **Fixed** (22ce3d3): any non-Latin script gets the English-only notice. The notice has no configured contacts |
| 30 | Moderation: shared thread pool, no hard HTTP timeout, a pipeline error could drop a moderation crisis verdict | **Fixed** (22ce3d3): own four-thread pool, HTTP call capped at 5 s, a verdict is consulted before an error goes out |
| 31 | Privacy: the Guide's Ollama address followed a dashboard edit of `OLLAMA_BASE_URL`; the hosted-provider list was short and not IDNA-folded; `-cloud` models were allowed | **Fixed** (22ce3d3): primary, fallback and moderation addresses are environment-only; the list is wider and checked after IDNA folding (it is still not exhaustive); `-cloud` is refused |
| 32 | Privacy: a 422 from `/priest/ask` echoed input; `/metrics` gave exact crisis and deferral counts; admin usage showed latency and zero counts | **Fixed** (22ce3d3): the 422 reports only where and what; crisis and deferral share one `fixed_reply` label; usage withholds latency until the total reaches the cohort and always lists `other` |
| 33 | Privacy copy: crisis and booth templates claimed the company cannot see the conversation or that the booth is anonymous; the Privacy panel named only some servers | **Fixed** (22ce3d3): templates corrected; the panel names every server and the moderation call |
| 34 | Status: `/priest/status` carried no crisis contacts, so the app could not show help before a question | **Fixed** (22ce3d3): it carries the configured contacts |
| 35 | Grounding: quotes inside a point were not checked against the cited source; third-level verse numbers; contracted verdicts; web addresses, emails and phones in the Guide's own words; canary written with JSON escapes; leak check flagged the worked examples; persona name could carry instructions; refusals counted twice | **Fixed** (22ce3d3): each of these handled, and the persona name is letters, spaces, apostrophes and hyphens only |
| 36 | Chat client: no connect timeout, the fallback got the full time, compression on, raw httpx errors | **Fixed** (22ce3d3): connect within 3 s, the fallback gets only the time left, compression off, any httpx error becomes a fixed failure |
| 37 | Admin API: config writes and legacy-key reindex and activate were not audited; the health badge did not probe the Ollama the embedder uses; the builder ignored the dashboard's embedding model; the admin index view held a second copy of the index | **Fixed** (d4db828): those writes are audited by key and action (never the value), the badge probes the Ollama the embedder uses (the environment's `OLLAMA_BASE_URL`), the builder gets the dashboard's embedding model, the admin view shares the loaded index |
| 38 | Knowledge pipeline: the preflight missed unquoted secrets and Bangladesh mobile numbers; BM25 did not fold transliterations; the "covered" decision grew with question length and counted chunks the model never sees; activation ignored a running build; the build lock was not a kernel lock; the cleaner could hang or fail on odd notes | **Fixed** (d4db828): preflight widened; BM25 folds transliterations; "covered" is judged on the chunks returned and a note's best four terms; activation takes the build lock and is refused (409) during a build; the lock is a `flock`; the cleaner's regexes are bounded and oversized or malicious notes are excluded |
| 39 | Mobile: the crisis haptic fired on every keystroke; help was not always visible; errors could leave a distressed person with no route to help; Clear kept recording; rate-limit wording; copy claimed "not stored" | **Fixed** (4d600a0): the haptic fires once per card; an always-visible help block and a "Need help now?" toggle; every error ends with the emergency-number line; Clear stops recording; waits read in minutes and hours; copy corrected |
| 40 | Clinician and HR review of the crisis, abuse, medical and legal lexicons and templates | **Open** |
| 41 | Native-speaker review of the romanised-Bangla crisis lines; romanised Bangla beyond the short list is not covered | **Open** |
| 42 | The English-only notice has no configured contacts; it points to the emergency number only | **Open** |
| 43 | Deferral-matched questions are not moderated (moderation runs on the pass path only) | **Fixed** (plan 14.3): a deferral question is moderated and a crisis verdict returns the fixed crisis reply; the check has its own two-thread pool and a two-check cap with no queue, so a flood of deferral questions (which skip the rate limiter) cannot starve the pass path's moderation, and past the cap a deferral goes out unmoderated as before |
| 44 | `PRIEST_MAX_CONCURRENCY` is read once, when the service is built | **Fixed** (plan 14.5): the limit is read on every request; raising it applies to the next question, lowering it never cancels one already running |
| 45 | `priest_service.py` is about 790 lines, over the 400-line rule; a split is planned | **Open** |
| 46 | The per-device limiter identity is client-chosen (a client can vary the header); IP limiting is not added | **Open** |
| 47 | Model-server request logging is unverified, for the vLLM server and for each Ollama | **Open** |
| 48 | vLLM is reached over plain HTTP in the prototype | **Open**, accepted for the prototype (finding 19) |
| 49 | The app renders its own fixed crisis wording plus the server's contacts; the server's crisis template text is not shown | **Open** |
| 50 | NFC and NFD path normalisation in the index | **Fixed** (plan 14.6): note paths are reported and chunked in NFC whatever the disk stores |
| 51 | The app-switcher snapshot of the question is not addressed | **Fixed, not yet checked on a device** (plan 16.6): the Guide screen sets `FLAG_SECURE` through `expo-screen-capture` while it is open (owner approved the dependency 2026-10-01), which blanks the Android snapshot and blocks screenshots and screen recording there; on iOS it blocks screen recording, and a plain cover drawn while the app is not active (iOS reports `inactive` before the snapshot) keeps the question out of the switcher. The citation sheet is hidden while covered. Leaving the screen also aborts an in-flight voice upload and its polling. Device check: 17.6 |
| 52 | `PUT /admin/config` does not validate Guide keys | **Fixed** (plan 14.4): every Guide key is checked on write (switch, persona name, model names, numeric ranges shared with the live accessors, tradition ids); a refusal is a 422 that names the key and the rule, never the value |
| 53 | The query embedding is sent to the Ollama at `OLLAMA_BASE_URL` (environment) without the hosted-provider or plain-http check that the chat servers and moderation get | **Fixed**: `LiveRetriever` runs the same address check and the `-cloud` refusal before it builds the embedder (found while writing this addendum). The index build still embeds vault text, not questions, without that check. |
| 54 | The retrieval and bench numbers in `docs/priest-retrieval-report.md` and `docs/priest-bench-report.md` predate the change to the "covered" rule and the evaluation scoring (d4db828) and must be regenerated | **Open**, needs a machine with memory (user action) |
| 55 | Verification review of the fixes (commit d6babc6): false crisis and deferral replies on scripture and everyday sentences, Greek and Hebrew terms refused, verse lists read as phone numbers, tradition mapping, index load on the event loop, config change committed before its audit row | **Fixed** (d6babc6) |
| 56 | Verification review, not fixed: ASCII single quotes can still cut a plural possessive ("companions' views") into a false quotation and fail a point (a regeneration); the per-device limiter gives fixed replies no ceiling, so a crafted Latin-expanding question costs up to about 60 ms of CPU per request; the lock probe can briefly refuse a builder that starts at the same instant; `is_fixed_reply` and `answer` route the question twice; `/confessions` validation errors still echo the transcript; unclosed code fences in a note are counted in the build stats but not named per note | **Open**, except the quote item: **decided, kept strict** (plan 14.9). A stray quote mark closed by a plural possessive is indistinguishable from a genuine quotation ending in a plural noun, so it stays checked; a false alarm costs one regeneration, a missed invented quotation would break grounding. Pinned by `test_a_stray_mark_closed_by_a_plural_possessive_is_still_checked`. |
| 57 | Insights needs history that outlives `RETENTION_HOURS` (plan 14.1, owner decision 2026-10-01) | **By design** (plan 14.12): just before the retention job removes or empties a confession it adds the confession's metadata to `insight_daily_counts`: the UTC day it was made and one label each for category, sentiment, department, status, delivered or not, and a delivery-time band. No text, no ids, no device hash, no time of day. The counts are kept indefinitely and stored exactly, so anyone with database access (the Privacy panel's `db_access` limit) can read a small count; the cohort rule is applied when Insights reads them. Withdrawn confessions are not counted |
| 58 | The masked recording was returned as base64 JSON (about 33.6 MB for five minutes), held in the phone's memory (plan 16.4) | **Changed** (plan 16.4): the app asks for `delivery=download`; the server holds the masked WAV in a temp file for one fetch by the uploading device, for at most two minutes, then deletes it (on the fetch, or on expiry); a wrong device gets the same 404 as an unknown id. At most 8 files wait at once. The default base64 reply is kept for older app builds. The unmasked upload is still deleted straight after masking |
