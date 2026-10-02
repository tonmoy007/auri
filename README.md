# 🕯️ Auri

**Whisper in the ear.** An anonymous AI-driven confession booth for internal teams.

Speak your truth in a candlelit 3D booth. AI listens, processes, and lets you forward anonymously or delete. Your voice is masked and no name is asked for.

## ✨ Features

**For employees (the mobile app)**

- **Immersive 3D Booth** — Interactive candlelit confessional built with React Three Fiber, with a door, drifting dust, a voice-responsive ring and a camera dolly while you speak
- **3 Environments** — Classic booth, forest glade, rooftop at night
- **AI STT/TTS Agent** — Whisper transcription (run in the background for long recordings, up to 5 minutes) and an Edge-TTS voice response
- **Voice Modulation** — 5 voice masks (Warm, Robotic, Ethereal, Deep, Random) via SoX, with the masked audio available to review before sending
- **Anonymity Modes** — Fully blind or "someone in your team" context — your choice at send-time
- **Forward or Delete** — Send to a department, or delete it
- **A reply that comes back** — After a confession the app shows a short supportive response. Crisis content gets a fixed message with configured contacts, never a generated one. HR can later write an anonymous organisational reply, shown in your history
- **Guide (off by default)** — An AI companion that answers questions from a study library of world religions and cites the notes it used. Auri keeps no questions or answers; the model server's own logging is not yet verified (prototype). Crisis questions, and questions in a script it cannot read, get a fixed message with the organisation's contacts, never a generated one. It stays behind a kill switch until the pilot checklist passes

**For staff (the dashboard)**

- **Roles** — `admin`, `hr` and `moderator`, each seeing only their own tabs. Sign in with a password or any OpenID Connect provider (Entra ID, Google Workspace, Okta, Keycloak)
- **Queue** — Moderation of AI-flagged content, with crisis items pinned to the top. Moderation also still works from the Telegram bot
- **Insights, Themes and Delivery** — Counts by week, category and department with small groups hidden, recurring themes with a weekly digest, and a view of what was forwarded where and what is stuck
- **Replies and Directory** — Write anonymous organisational replies; manage the departments confessions are forwarded to
- **Privacy and Audit** — The live retention settings and what is and is not protected, plus an append-only log of every read of confession content
- **Admin tools** — Live config (LLM chain, STT model, voice masks), ngrok and LiveKit status, APK builds, and the Guide's index and usage

**Delivery**

- **Telegram Delivery** — Confessions are posted to the chosen department's Telegram chat
- **Moderation** — AI-flagged content is held for review before delivery

## 🏗️ Architecture

| Layer | Stack |
|-------|-------|
| Mobile | React Native + Expo (SDK 52) |
| 3D UI | React Three Fiber + drei + Three.js |
| Backend | FastAPI + SQLAlchemy + Alembic |
| Staff dashboard | Vite + React 19 + TypeScript + Tailwind 4 + shadcn/ui |
| STT | OpenAI Whisper / faster-whisper |
| TTS | Edge-TTS |
| Voice Mod | SoX pitch/formant |
| AI Agent | Local Ollama first, with Gemini or OpenAI as configurable fallbacks (Claude optional) |
| Guide and themes | Any OpenAI-compatible chat server (for example vLLM), with a local Ollama fallback |
| Real-time voice | Self-hosted LiveKit SFU + LiveKit Agents worker (connectivity only so far) |
| DB | PostgreSQL |
| Delivery | Telegram Bot (python-telegram-bot), NATS |
| Observability | Prometheus `/metrics`, optional Sentry, structured logs |
| CI/CD | GitHub Actions: lint, tests (migrations applied and reversed on Postgres), dashboard, build, Trivy; gated deploy to GHCR |

One deployment serves one organisation: see [`docs/adr/tenancy.md`](docs/adr/tenancy.md).

## 📱 The Flow

```
[Enter Auri] → Pick Voice Mask → AI greets you
    ↓
[Speak] → The recording is voice-masked and transcribed in the background
    ↓
[AI processes] → Replaces recognised details, categorizes, summarizes, checks for crisis or policy issues
    ↓
[Review] → Transcription | AI Summary | Voice-masked Audio
    ↓
[Choose] → Fully blind / "Someone in your team"
    ↓
[Act] → Send | Forward to a department | Delete
    ↓
[Reply] → A supportive response now; an HR reply later, if there is one
```

## 🗺️ Status

Work is organised into phases tracked in [`.hermes/plans/`](.hermes/plans/) (the plan, and `tracking.json` for the live task status). Roughly:

| Phases | What | State |
|--------|------|-------|
| 1-5 | 3D booth, recording and voice masking, AI agent, Telegram bot, environments | Done, except real sound design (needs audio assets) and the optional RVC voice conversion |
| 6 | Data lifecycle, observability, release readiness | Mostly done; store metadata and device end-to-end tests still open |
| 7 | Live LiveKit voice conversation | Connectivity only; the conversation itself is not built |
| 8-10 | App settings, review fixes, local dev stack and config dashboard | Done |
| 11 | HR operations dashboard: roles, audit, insights, queue, delivery, replies, themes, privacy panel | Done |
| 12 | Counselor response quality: prompt files, the deterministic crisis reply, the evaluation set and the scoring harness are done; structured replies, guardrails and a model bench are next | In progress |
| 13 | Guide over the study library | Built; the mobile and real-stack verification is open |
| 14-17 | Privacy and safety debt, release hygiene, recording robustness, Guide quality gates | Mostly done; the Guide pilot is a no-go until its gates are met ([checklist](docs/runbooks/priest-launch-checklist.md)) |

## 🛡️ Privacy

- No user accounts and no name asked for — a one-way code made on the device is kept with each confession, so someone with database access could tell which confessions came from one phone (set `DEVICE_HASH_PEPPER` to make that code useless on its own)
- Audio is processed in temporary files on the server and deleted afterwards (if a speech fallback provider is configured, the audio may be sent to it); the phone deletes its own copies (the unmasked recording once masking succeeds, the rest when a confession is sent or deleted, or at the next app start)
- Recognised personal details are replaced before the text is stored (by the model, or by simple pattern matching if it fails); it can miss some, so what a confession says can still point to its author
- Nothing is encrypted at the column level; protect the database and its backups accordingly
- The recipient receives no sender name or device details, but sees a summary and up to the first 1,000 characters of the transcript; HR sees the summary, category and exact send time of every confession that is not deleted, and staff can read the full text of held and forwarded ones
- Most HR screens show the summary, category and mood. Opening the full transcript of an item held for review from an HR screen needs a written reason, which is logged; the Queue tab shows moderators and HR the full text of held items without one. Every dashboard read of confession content is recorded in an audit trail that admins can read; reads through Telegram are not
- Counts and charts hide any group smaller than `ANALYTICS_MIN_COHORT` (default 5), so a small department cannot be picked out
- Every confession is purged `RETENTION_HOURS` after its last change, whatever its status, once the retention job runs. A confession with an unread HR reply is kept as a reply-only shell for `REPLY_RETENTION_DAYS`
- The dashboard's Privacy tab states what is kept, for how long, and what is not protected, from the live configuration. The full review is in [`docs/privacy-review.md`](docs/privacy-review.md)

## 🗂️ Project Structure

```
auri/
├── backend/          FastAPI app (API, DB models, services, LiveKit agent worker)
│   ├── app/
│   │   ├── api/v1/       REST endpoints (confessions, moderation, delivery, hr, audit, auth, admin, priest, ...)
│   │   ├── services/     LLM, STT, TTS, voice-mod, retention, insights, themes, audit, auth
│   │   ├── priest/       The Guide: retrieval, answer validation, safety routing
│   │   ├── llm/prompts/  Versioned prompt files (.md with frontmatter)
│   │   ├── models/       SQLAlchemy models
│   │   └── agent.py      LiveKit Agents worker entrypoint
│   ├── alembic/           DB migrations
│   ├── scripts/           Evaluation scripts
│   └── tests/
├── bot/               Telegram delivery/moderation bot (python-telegram-bot)
├── mobile/            Expo / React Native app (the confession booth UI)
├── dashboard/         Staff dashboard — Vite + React + shadcn/ui
├── docs/              ADRs (docs/adr), runbooks (docs/runbooks), privacy review and reports
├── .hermes/plans/     The implementation plan and live task tracking
├── .github/workflows/ CI, gated deploy, and a latest-dependencies check
├── docker-compose.yml Full local stack: db, nats, api, bot, livekit, agent, ollama
├── Dockerfile.api / Dockerfile.agent / bot/Dockerfile
├── AGENTS.md          Development rules (read this before contributing)
└── Makefile           Shortcuts for everything below
```

## 🚀 Getting Started

### Prerequisites

- Docker + Docker Compose (for Postgres, NATS, and the optional LiveKit/agent/Ollama services)
- Python 3.11+ and a virtualenv tool (the repo's own `.venv` works — `python3 -m venv .venv`)
- Node.js 22 (the version in `.nvmrc`; the dashboard's test tools need 22.22.2 or newer) and npm
- [Expo CLI](https://docs.expo.dev/get-started/installation/) (`npx expo`) for the mobile app; Android Studio/SDK if you want to build/run on Android
- (Optional) [ngrok](https://ngrok.com/) — needed if a real phone or an Android emulator (which cannot reach your machine's LAN IP directly) needs to reach your local backend

### 1. Clone and configure

```bash
git clone git@github.com:tonmoy007/auri.git
cd auri
cp .env.example .env
```

Edit `.env` and fill in real values — see [Configuration](#-configuration) below for what each block does. At minimum for local dev you can leave the LLM/Telegram keys as placeholders; the app degrades gracefully (LLM chain tries Ollama → Gemini → OpenAI, first configured one wins).

### 2. Start the core stack (Docker)

```bash
docker compose up -d db nats api bot
```

This builds and starts Postgres, NATS, the FastAPI backend (`http://localhost:8000`), and the Telegram bot. First boot in `ENVIRONMENT=development` auto-creates DB tables; for anything past a quick trial, run migrations explicitly:

```bash
make db-migrate          # or: cd backend && alembic upgrade head
```

Optional services live behind Docker Compose's `optional` profile — nothing you don't ask for:

```bash
# Self-hosted LiveKit SFU (Phase 7) + the LiveKit Agents worker (task 10.7)
docker compose --profile optional up -d livekit agent

# Local LLM via Ollama, instead of a cloud provider
docker compose --profile optional up -d ollama
```

Check everything is up: `curl http://localhost:8000/health` → `{"status":"ok"}`.

### 3. Run the backend without Docker (faster iteration)

```bash
python3 -m venv .venv && source .venv/bin/activate
make install-backend
make db-migrate
make dev-backend          # uvicorn --reload on :8000
```

Point `DB_HOST`/`DATABASE_URL` at `localhost` (not `db`) when running this way — `.env`'s defaults assume the Docker network's service name.

### 4. Run the LiveKit Agents worker (Phase 7 / task 10.7)

Requires the `livekit` service (self-hosted SFU) to be up first:

```bash
docker compose --profile optional up -d livekit
make dev-agent             # python -m app.agent dev
```

This is connectivity wiring only today — it authenticates against LiveKit and joins a room; the real STT/LLM/TTS conversation lands in a later task (7.3+).

### 5. Run the mobile app

```bash
make install-mobile        # cd mobile && npm install
cd mobile && npx expo start
```

Press `a` for Android or `i` for iOS. The app's build-time backend URL comes from `EXPO_PUBLIC_API_URL`/`EXPO_PUBLIC_WS_URL` (see `mobile/src/config/api.ts`) — but you don't need to rebuild to change it: **Settings → Developer → Backend URL** lets you override it live on an already-installed app (persisted via `expo-secure-store`, survives app restarts). This is how you point a running app at an ngrok tunnel instead of your LAN IP — useful since Android emulators can't reach your machine's real LAN IP by default (physical devices on the same Wi-Fi can, though).

### 6. Run the staff dashboard

```bash
cd dashboard && npm install && npm run dev
```

Opens on `http://localhost:5173`. Enter your backend URL, then sign in:

- **Password** — the first admin comes from `ADMIN_BOOTSTRAP_EMAIL` and `ADMIN_BOOTSTRAP_PASSWORD` in `.env`, applied once while there are no users. That admin creates every other account and sets its role (`admin`, `hr` or `moderator`)
- **Single sign-on** — set the `OIDC_*` variables to let staff sign in through an OpenID Connect provider. An admin still creates each account first. See [`docs/adr/staff-sso.md`](docs/adr/staff-sso.md)
- **Shared admin key** — `ADMIN_API_KEY` still works as an escape hatch and shows every tab. Prefer named accounts, which leave an audit trail

Each role sees only its own tabs: HR sees Insights, Queue, Directory, Delivery, Replies, Themes and Privacy; a moderator sees Queue; an admin sees Config, Status, Build, Guide, Audit and Privacy.

The deploy workflow builds the API and bot images only; the dashboard is not part of it.

### 7. Everyday commands

```bash
make lint          # ruff + mypy (backend/bot) + eslint + tsc (mobile)
make lint-fix       # auto-fix what ruff can
make test           # pytest backend/ bot/ with coverage
make ci             # every CI check locally: Python, dashboard and mobile
make db-revision msg="what changed"   # new Alembic migration
make db-rollback    # undo the last migration
make docker-logs     # tail every running container
make clean           # nuke caches, venvs, node_modules, build output
```

Every commit is expected to pass lint + its own tests — see `AGENTS.md` for the full workflow, commit and testing rules this repo follows, and `ORCHESTRATOR.md` for how plan tasks are tracked.

## 📚 Documentation

- [`AGENTS.md`](AGENTS.md) and [`ORCHESTRATOR.md`](ORCHESTRATOR.md) — development rules and plan discipline
- [`docs/adr/`](docs/adr/) — decisions: staff SSO, one company per deployment, the recording limit, the Guide
- [`docs/runbooks/`](docs/runbooks/) — deploy and rollback, the Guide's model server, the Guide pilot checklist
- [`docs/privacy-review.md`](docs/privacy-review.md) — what each finding was and where it stands

## ⚙️ Configuration

All configuration is environment-variable driven — `.env.example` (repo root) is the canonical reference, fully commented block-by-block (database, security, Telegram, admin dashboard, LLM provider chain, LiveKit, STT/TTS, observability, CORS/rate-limiting). Copy it to `.env` and never commit real secrets — `.env` is gitignored, `.env.example` must only ever contain placeholders.

A few settings worth knowing about specifically:

- **`LLMService(provider="auto")`** tries Ollama (local/free) → Gemini → OpenAI, first non-empty reply wins. Claude is available but only via explicit `provider="claude"`, never part of the auto chain.
- **LiveKit** defaults (`LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`) match the self-hosted `livekit` Docker service's `--dev` mode (`devkey`/`secret`) — no cloud account needed for local dev.
- **DB-backed live config** — LLM provider/model, `WHISPER_MODEL`, voice-mask effect chains, crisis contacts and the minimum group size can be overridden at runtime from the dashboard without restarting the backend (DB-first, `.env`/`Settings()` as fallback).
- **Staff access** — `ADMIN_BOOTSTRAP_EMAIL`/`ADMIN_BOOTSTRAP_PASSWORD` create the first admin; `SESSION_TOKEN_SECRET` signs sessions (signing refuses to run outside `ENVIRONMENT=development` without a real one); the `OIDC_*` block turns on single sign-on and can map a provider claim to a role. `ADMIN_API_KEY` is the legacy shared secret for `/api/v1/admin/*` — generate a real random value, don't ship the placeholder.
- **Data retention** — `RETENTION_HOURS`, `REPLY_RETENTION_DAYS` and `DEVICE_HASH_PEPPER` (at least 32 characters). Run the purge with `python -m app.services.retention` on a schedule.
- **Guide and themes** — `PRIEST_MODE_ENABLED` is off by default. `PRIEST_*` and `THEMES_LLM_*` point at an OpenAI-compatible server; plain `http` to a public address is refused unless you opt in. `CRISIS_HELPLINE_NAME`, `CRISIS_HELPLINE_NUMBER` and `CRISIS_EAP_CONTACT` fill the fixed crisis reply.
- **Observability** — `GET /metrics` needs `METRICS_API_KEY` as a bearer token when set; `SENTRY_DSN` is optional. Leave `SQL_ECHO` off anywhere real confessions are stored.

---

*"Auri" — from Latin auricula (ear), auricular confession (whispered in the ear).*
