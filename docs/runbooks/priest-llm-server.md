# Runbook: the chat server behind the Guide, and the vault

This page covers the model server the Guide asks, how the study vault gets to the server, and what to harden before anything beyond a prototype.

## Where the Guide sends questions

Order of resolution, from `app/llm/chat_endpoint.py`:

1. `PRIEST_LLM_BASE_URL` (with `PRIEST_LLM_API_KEY`), if set.
2. **Prototype shortcut:** if it is empty, the same server and opt-ins as theme grouping (`THEMES_LLM_BASE_URL`, `THEMES_LLM_USE_OPENAI_API_KEY`, `THEMES_LLM_ALLOW_INSECURE_HTTP`), with the model from `PRIEST_LLM_MODEL`.
3. Local Ollama (`PRIEST_FALLBACK_BASE_URL`, or `OLLAMA_BASE_URL` plus `/v1`) only if `PRIEST_FALLBACK_MODEL` is set.

The address, the key and the paths can only be set in the environment, never from the dashboard. Hosted providers (OpenAI, Gemini, Anthropic) are refused and reported on the Privacy panel.

## Current prototype state (accepted)

The vLLM box is reached over plain HTTP on a public address with an API key. That means a Guide question and the key cross the internet unencrypted, anyone can reach the GPU port, and the operator can see questions while they are answered. This was accepted for the prototype. Do not enable the Guide for real users in this state.

## Before a pilot

1. Put the server behind TLS, or reach it through an SSH tunnel or WireGuard, and set `PRIEST_LLM_BASE_URL` to that address.
2. Start vLLM with `--api-key`, and with prompt and request logging off (the flag name differs between vLLM versions: `--disable-log-requests` in older ones; check the deployed version's `--help`).
3. Firewall the GPU port from the public internet.
4. Start with `--max-model-len` of at least 8192; a Guide prompt is about 3.5k tokens.
5. Confirm the served model name matches `PRIEST_LLM_MODEL`, and check how the model handles thinking (the client sends `enable_thinking: false` and strips `<think>` blocks).
6. Decide who operates the box and record it in `docs/privacy-review.md`.

## Checking it

- Dashboard, Guide tab: reachability badges come from `GET /admin/priest/health`. A 401 from the server reads as unreachable.
- Smoke: `PRIEST_MODE_ENABLED=true python3 backend/scripts/priest_smoke.py` asks three fixed questions through the real service and prints only the kind and cited titles.
- Live test (opt in, never in CI): `RUN_LIVE_LLM=1 python3 -m pytest backend/tests/live -q`.

## Getting the vault to the server

1. `backend/scripts/priest_vault_preflight.py <vault>/religion-study` prints counts only and must pass. It refuses on secrets, private keys, emails, phone numbers, denied paths or symlinks. Notes with no `type:` block the sync; notes with broken frontmatter are excluded from the index, so fix them in the vault.
2. `scripts/priest-vault-sync.sh` (try `--dry-run` first) sends only `religion-study/**/*.md`.
3. In the dashboard, Guide tab, start a reindex. The builder needs local Ollama reachable for embeddings. A failed build leaves the active index untouched. Roll back by activating an earlier version.

Keep `.obsidian/`, `.smart-env/`, `.claude/`, `.omc/`, `.git/` and `assets/` off the server; the plugin folders can hold API keys.
