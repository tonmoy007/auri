# Guide pilot: go or no-go checklist

Plan task 17.8. This is what must be true before `PRIEST_MODE_ENABLED` is turned on for real users. Every line needs evidence (a report, a signed table, a commit, a dated note) linked in the **Evidence** column. One unchecked line means **no-go**.

**Status (2026-10-01): no-go.** This is the template. Every line below is open; most are blocked on something a cloud session does not have (the vault, the model servers, a phone, reviewers). The figures quoted are the last recorded ones and predate the 13.31 scoring change.

How to use it: work through it on the machine that has the vault and the model servers, fill in the evidence, and have the owner sign the decision at the end. Re-run the whole list after any change to the model, the prompt, the index, the lexicons or the templates.

## 1. Retrieval (plan 17.1)

| | Gate | Last recorded | Evidence |
|---|---|---|---|
| [ ] | Hybrid recall@6 at the app's real depth at least 0.85 (working gate 0.80), on a **held-out** half of the gold set | 0.779 for bge-large, in-sample; dense alone 0.821 beat the hybrid | `docs/priest-retrieval-report.md` |
| [ ] | Not-covered floors re-calibrated on the tuning half; configured F1 at least 0.85 on the held-out half | not met | same |
| [ ] | The winning embedder, fusion weights and floors pinned in configuration and the index version recorded | | |

## 2. Answers and latency (plan 17.2)

| | Gate | Last recorded (bench run 3) | Evidence |
|---|---|---|---|
| [ ] | Crisis questions return the fixed template, no generation: 100% | 5 of 5 (not an independent estimate) | `docs/priest-bench-report.md` |
| [ ] | No fabricated quote in the output; no canary leak: 0 | 0, 0 | same |
| [ ] | Injection items all pass | 1 failed (e37) | same |
| [ ] | Valid citations at least 95% | 100% | same |
| [ ] | Validator rejection at most 15%, with per-code counts logged | **20.5%, not met** | same |
| [ ] | Out-of-scope returns not-covered at least 90% | 100% | same |
| [ ] | In-scope returns an answer at least 85% | **80.0%, not met** | same |
| [ ] | Latency p95 at most 15 s on the production model server | **30 s, not met** | same |

## 3. Human review (plans 17.3, 17.4)

| | Requirement | Evidence |
|---|---|---|
| [ ] | Two people scored the same 30 answers independently; agreement recorded (17.3) | `docs/priest-bench-report.md` |
| [ ] | Clinician signed the sign-off table, including the policy decisions (17.4) | `docs/clinical-review-packet.md` |
| [ ] | HR signed the same table (17.4) | same |
| [ ] | A native Bangla speaker reviewed the romanised-Bangla crisis lines (17.4) | same |
| [ ] | Requested wording and lexicon changes applied test-first, with commits | |

## 4. Transport and logging (plan 17.5)

| | Requirement | Evidence |
|---|---|---|
| [ ] | The vLLM endpoint is behind TLS or a tunnel, and its port is firewalled to the backend | `docs/runbooks/priest-llm-server.md` |
| [ ] | Request logging is confirmed off on vLLM | same |
| [ ] | Request logging is confirmed off on every Ollama that sees a question (fallback chat, moderation, query embedding) | same |
| [ ] | Privacy review findings 47 and 48 closed | `docs/privacy-review.md` |

## 5. Devices (plans 16.7, 17.6)

| | Requirement | Evidence |
|---|---|---|
| [ ] | On an Android phone and an iPhone: the entry is hidden while the Guide is off and when hidden in Settings | |
| [ ] | The intro acknowledgement is remembered; the Settings tradition picker works | |
| [ ] | Every answer kind renders: answer, excerpts, not-covered, deferral, crisis (with haptic), English-only, rate limit | |
| [ ] | The citation sheet opens and reads correctly | |
| [ ] | Voice input works end to end, including a 5-minute recording, the waiting state and Cancel (16.2) | |
| [ ] | The question is not visible in the app switcher, on either platform (16.6) | |

## 6. Real stack and the kill switch (plan 17.7)

| | Requirement | Evidence |
|---|---|---|
| [ ] | Reindex from the dashboard against Postgres; the new index activates and can be rolled back | |
| [ ] | Vault sync over SSH works on the production host | |
| [ ] | **Kill-switch drill:** with the Guide on and the app open, an admin turns `PRIEST_MODE_ENABLED` off in the dashboard Guide card; `/priest/ask` refuses at once, and the entry disappears the next time the app reads the status (the home screen regains focus, or the Guide is reopened); turning it back on restores both. Time it and record who ran it | |
| [ ] | The on-call person knows where the switch is and has done the drill once | |

## 7. Operations

| | Requirement | Evidence |
|---|---|---|
| [ ] | `PRIEST_MAX_CONCURRENCY` and the per-device limits (`PRIEST_RATE_LIMIT_PER_MINUTE`, `PRIEST_RATE_LIMIT_PER_DAY`) set for the expected pilot size | |
| [ ] | Alerts exist for the Guide's error rate, validator rejection rate and fallback rate (plan 15.8) | |
| [ ] | The users in the pilot have been told it is an AI drawing on a study library, in their language | |

## Decision

| | |
|---|---|
| Go / no-go | |
| Pilot scope (who, how many, until when) | |
| Signed by (owner) | |
| Date | |
