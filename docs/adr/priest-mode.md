# ADR: Priest mode (the "Guide")

- **Status:** accepted for a prototype, 2026-10-01.
- **Design record:** `.hermes/plans/proposals/priest-mode-plan.md` has the full analysis; this is the decision.

## Decision

Priest mode is an in-process `backend/app/priest/` package in the FastAPI backend. "Priest" is the code name only; users see a neutral, configurable name (default "Guide") and are told it is an AI that draws on a study library, not clergy, counselling or advice.

- **Knowledge:** an allowlisted, markdown-only copy of the `religion-study/` Obsidian vault (an academic comparative study of about 12 traditions, not pastoral text) is cleaned, chunked and embedded offline into an immutable, versioned file index that is activated atomically and can be rolled back.
- **Retrieval:** hybrid in-house BM25 (diacritic-folded, verse references kept whole) plus dense cosine over local Ollama embeddings, fused with reciprocal rank fusion, with a relevance floor so off-topic questions are not answered.
- **Generation:** an async OpenAI-compatible client to a vLLM server, with local Ollama as the fallback. The chat server addresses come from the environment only, so the operator decides where a question goes. Two checks back that up: a list of well-known hosted providers (`chat_endpoint.THIRD_PARTY_HOSTS`, matched after IDNA folding, subdomains included) is refused, and so is any model name ending in `-cloud`, which Ollama forwards to a third party (`chat_endpoint.refuse_cloud_model`). The list is not exhaustive and is not the safeguard; the environment-only address is.
- **Output:** non-streaming JSON validated before any word reaches the user: citations must be real, every quote verbatim in its cited chunk, scripture references present in the sources, banned patterns absent, no canary or instruction leakage. The quote label comes from note metadata, never the model. A failed answer is regenerated once, then replaced by deterministic library excerpts.
- **Safety:** crisis, medical, legal, abuse and judge-a-person questions are routed by versioned lexicons before any retrieval or generation; crisis returns a fixed template with configured contacts. For questions not already routed, a parallel moderation call on the operator's Ollama can also force it. That call reads the question as typed, before de-identification; it runs on the pass path, and since plan 14.3 also on a question the lexicon deferred (a crisis verdict then returns the crisis template), but not for one already sent to crisis or the English-only notice. The pass path's thread pool is bounded (four threads); the deferral check has its own two threads and a cap of two checks that never queues, because deferral replies skip the rate limiter, so past the cap a deferral goes out unmoderated. The HTTP call is capped at 5 s, and a failure counts as `policy` on the pass path and as a plain deferral on the deferral path, never as a crisis verdict. A question in a non-Latin script gets a fixed English-only notice before retrieval or any model call. There is no human escalation, because nothing is stored.
- **Privacy:** no tables, no stored questions or answers, metadata-only logs, fixed metric labels, a per-device rate limit keyed by an HMAC with a per-process salt (plus a wider per-address window when `TRUSTED_PROXY_HEADER` names the proxy's header, plan 14.7), and no staff surface that shows content. Crisis, deferral and unsupported-script replies are never held to the question limits; they have only a high flood ceiling of their own (20 a minute and 200 a day per device, 100 and 2,000 per address). `/metrics` folds crisis and deferral into one `fixed_reply` label; the admin usage view withholds latency until the total reaches the cohort and always lists an `other` row.
- **Switch:** off by default; `PRIEST_MODE_ENABLED` is a dashboard-editable kill switch. The Guide's chat server addresses, key and paths are environment-only: `PRIEST_LLM_BASE_URL`, `PRIEST_LLM_API_KEY`, `PRIEST_FALLBACK_BASE_URL` (when empty, the fallback is `OLLAMA_BASE_URL` from the environment plus `/v1`, not the dashboard layer), the moderation call (`OLLAMA_BASE_URL` and `OLLAMA_MODEL` from the environment) and the vault and index paths. `OLLAMA_BASE_URL` is still editable in the dashboard, but that edit only affects confession moderation and other non-Guide paths. `PRIEST_LLM_MODEL` and `PRIEST_FALLBACK_MODEL` stay dashboard-editable as model names; a name ending in `-cloud` is refused.

## Owner decisions (2026-09-30)

D1 neutral name; D2 all traditions with an on-device filter; D3 the agent lives in the Auri backend and the GPU box only does inference, with the security hardening **deferred because this is a prototype**; D4 English only; D5 off by default; D6 a separate entry point that never receives confession text; D7 stateless; D8 voice input yes, **voice output undecided and absent**; D9 `religion-study/*.md` only; D10 fixed crisis template, no escalation; D11 regex-only de-identification of the question; D12 numpy pinned (PyYAML also pinned).

## Alternatives rejected or deferred

| Alternative | Why not now |
|---|---|
| A separate service on the GPU box | Splits the trust zone and safety code; the box is reachable over plain HTTP. The package boundary allows it later. |
| Extending the LiveKit agent | The real-time voice pipeline is not built. |
| pgvector | Needs an image change, and SQLite tests cannot create a vector column; the data is small and derived. |
| sqlite-vec | A native dependency for no gain at this size. |
| Dense-only retrieval | Proper nouns, diacritics and verse references need lexical matching. |
| Streaming tokens | Would bypass the validators. |
| Conversation memory | Would create stored religious data. |

## Deviations from project rules, on purpose

- **Retries:** one retry on a connect error only, never on a timeout, inside one deadline; a 30-second experience cannot absorb AGENTS §9.1's three attempts with backoff.
- **Configuration:** `Settings` plus the `app_settings` live layer, not YAML under `backend/config/`, because that is what the codebase actually does.
- **File size:** `priest_service.py` (about 790 lines) exceeds the 400-line rule; a split is planned.

## Consequences and known limits

- The index lives in API memory (about 20-40 MB); the rate limiter is per process and can be flooded to evict a real device's window. Its identity is the device header the client chooses, so a client that varies the header gets a fresh device window; since plan 14.7 a per-address window bounds that, but only when the deployment sets `TRUSTED_PROXY_HEADER` (off by default, environment only) and every request comes through the proxy that sets it. Without it there is no address limit, because behind a proxy the socket address is the proxy's and would put every user in one window. `PRIEST_MAX_CONCURRENCY` is read once, when the service is built.
- Latency is several seconds with staged status text; many questions will be "not covered" because the vault is academic and thin on pastoral content.
- **Prototype exposure:** a question is visible to the operator of each server that handles it while it is handled: the vLLM box (at present plain HTTP with an API key), the fallback Ollama if used, and the Ollama that does moderation (which reads the question as typed) and query embedding. Each server's own access log records the time and client address of a request, and the request time of a question also appears in Auri's API access log. Whether a model server logs prompts is not verified. Auri stores no question or answer. Crisis and deferral counts are folded in `/metrics`; differencing the admin usage counts over time can still show when a count crosses the cohort (admin-only; accepted for the prototype). See `docs/runbooks/priest-llm-server.md` and `docs/privacy-review.md`.
- **Open items from the independent review:** clinician and HR review of the crisis, abuse, medical and legal lexicons and templates; native-speaker review of the romanised-Bangla crisis lines, and romanised Bangla beyond a short list is not covered; the English-only notice has no configured contacts and points only to the emergency number. The full list is in `docs/privacy-review.md`.
- **Quotes are unverifiable beyond the vault:** the vault looks bulk-authored, so "verbatim in the note" proves the quote is in the vault, not that it is authentic scripture. The interface says "quoted in the note", never "scripture says".
- **Copyright** of the vault's quotations and of the sibling `scriptures/` folder is unreviewed and deferred; the sibling folder is excluded.

## Follow-ups

Bangla; follow-up context; voice output; public-domain verse verification; moving the service to the GPU box; request ids and async model calls for the confession flow. Config writes are audited by key and action (never the value) since commit d4db828.
