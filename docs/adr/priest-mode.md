# ADR: Priest mode (the "Guide")

- **Status:** accepted for a prototype, 2026-10-01.
- **Design record:** `.hermes/plans/proposals/priest-mode-plan.md` has the full analysis; this is the decision.

## Decision

Priest mode is an in-process `backend/app/priest/` package in the FastAPI backend. "Priest" is the code name only; users see a neutral, configurable name (default "Guide") and are told it is an AI that draws on a study library, not clergy, counselling or advice.

- **Knowledge:** an allowlisted, markdown-only copy of the `religion-study/` Obsidian vault (an academic comparative study of about 12 traditions, not pastoral text) is cleaned, chunked and embedded offline into an immutable, versioned file index that is activated atomically and can be rolled back.
- **Retrieval:** hybrid in-house BM25 (diacritic-folded, verse references kept whole) plus dense cosine over local Ollama embeddings, fused with reciprocal rank fusion, with a relevance floor so off-topic questions are not answered.
- **Generation:** an async OpenAI-compatible client to a vLLM server, with local Ollama as the fallback. It is never pointed at Gemini, OpenAI or Anthropic; a denylist enforces this.
- **Output:** non-streaming JSON validated before any word reaches the user: citations must be real, every quote verbatim in its cited chunk, scripture references present in the sources, banned patterns absent, no canary or instruction leakage. The quote label comes from note metadata, never the model. A failed answer is regenerated once, then replaced by deterministic library excerpts.
- **Safety:** crisis, medical, legal, abuse and judge-a-person questions are routed by versioned lexicons before any retrieval or generation; crisis returns a fixed template with configured contacts, and a parallel local moderation call can also force it. There is no human escalation, because nothing is stored.
- **Privacy:** no tables, no stored questions or answers, metadata-only logs, fixed metric labels, a per-device rate limit keyed by an HMAC, and no staff surface that shows content.
- **Switch:** off by default; `PRIEST_MODE_ENABLED` is a dashboard-editable kill switch; the chat server address, key and paths are environment-only.

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
- **File size:** `priest_service.py` exceeds the 400-line rule and is to be split.

## Consequences and known limits

- The index lives in API memory (about 20-40 MB); the rate limiter is per process and can be flooded to evict a real device's window.
- Latency is several seconds with staged status text; many questions will be "not covered" because the vault is academic and thin on pastoral content.
- **Prototype exposure:** questions travel to the vLLM box, at present over plain HTTP with an API key, and its operator can see them while they are answered. See `docs/runbooks/priest-llm-server.md` and `docs/privacy-review.md`.
- **Quotes are unverifiable beyond the vault:** the vault looks bulk-authored, so "verbatim in the note" proves the quote is in the vault, not that it is authentic scripture. The interface says "quoted in the note", never "scripture says".
- **Copyright** of the vault's quotations and of the sibling `scriptures/` folder is unreviewed and deferred; the sibling folder is excluded.

## Follow-ups

Bangla; follow-up context; voice output; public-domain verse verification; moving the service to the GPU box; request ids and async model calls for the confession flow; auditing config changes.
