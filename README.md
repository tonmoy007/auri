# 🕯️ Auri

**Whisper in the ear.** An anonymous AI-driven confession booth for internal teams.

Speak your truth in a candlelit 3D booth. AI listens, processes, and lets you forward anonymously or delete. Your voice is masked and no name is asked for.

## ✨ Features

- **Immersive 3D Booth** — Interactive candlelit confessional built with React Three Fiber
- **AI STT/TTS Agent** — Whisper transcription + Edge-TTS voice response
- **Voice Modulation** — 5 voice masks (Warm, Robotic, Ethereal, Deep, Random) via SoX
- **Anonymity Modes** — Fully blind or "someone in your team" context — your choice at send-time
- **Telegram Delivery** — Confessions delivered posted to the chosen department's Telegram chat
- **Moderation** — AI-flagged content queued to designated moderator for review
- **Guide (off by default)** — an AI companion that answers questions from a study library of world religions and cites the notes it used. Auri keeps no questions or answers; the model server's own logging is not yet verified (prototype). Crisis questions get a fixed message with contacts, never a generated one
- **Forward or Delete** — Send to a department, or delete it
- **3 Environments** — Classic booth, forest glade, rooftop at night

## 🏗️ Architecture

| Layer | Stack |
|-------|-------|
| Mobile | React Native + Expo |
| 3D UI | React Three Fiber + drei + Three.js |
| Backend | FastAPI + WebSocket |
| STT | OpenAI Whisper / faster-whisper |
| TTS | Edge-TTS |
| Voice Mod | SoX pitch/formant |
| AI Agent | Local Ollama first, with Gemini or OpenAI as configurable fallbacks (Claude optional) |
| DB | PostgreSQL |
| Delivery | Telegram Bot (python-telegram-bot) |

## 📱 The Flow

```
[Enter Auri] → Pick Voice Mask → AI greets you
    ↓
[Speak] → STT transcribes, and the recording is voice-masked
    ↓
[AI processes] → Replaces recognised details, categorizes, summarizes
    ↓
[Review] → Transcription | AI Summary | Voice-masked Audio
    ↓
[Choose] → Fully blind / "Someone in your team"
    ↓
[Act] → Send | Forward to a department | Delete
```

## 🗺️ Roadmap

| Phase | Days | What |
|-------|------|------|
| 1 | 2 | 3D Booth scene (candle, particles, rings, door) |
| 2 | 2.5 | Recording + Voice Modulation + STT |
| 3 | 2 | LLM Agent + TTS |
| 4 | 2.5 | Telegram Bot + Forward/Delete + Moderation |
| 5 | 1 | Environment variants + Haptics + Sound |

## 🛡️ Privacy

- No user accounts and no name asked for — a one-way code made on the device is kept with each confession, so someone with database access could tell which confessions came from one phone
- Audio is processed in temporary files on the server and deleted afterwards (if a speech fallback provider is configured, the audio may be sent to it); the phone deletes its own copies (the unmasked recording once masking succeeds, the rest when a confession is sent or deleted, or at the next app start)
- Recognised personal details are replaced before the text is stored (by the model, or by simple pattern matching if it fails); it can miss some, so what a confession says can still point to its author
- Nothing is encrypted at the column level; protect the database and its backups accordingly
- The recipient receives no sender name or device details, but sees a summary and up to the first 1,000 characters of the transcript; HR sees the summary, category and exact send time of every confession that is not deleted, and staff can read the full text of held and forwarded ones
- The dashboard's Privacy tab states what is kept, for how long, and what is not protected, from the live configuration

## 🗂️ Project Structure

```
auri/
├── backend/          FastAPI app (API, DB models, services, LiveKit agent worker)
│   ├── app/
│   │   ├── api/v1/       REST endpoints (confessions, moderation, delivery, admin, ...)
│   │   ├── services/     LLM, STT, TTS, voice-mod, live settings, retention
│   │   ├── models/       SQLAlchemy models
│   │   └── agent.py      LiveKit Agents worker entrypoint (Phase 7/10.7)
│   ├── alembic/           DB migrations
│   └── tests/
├── bot/               Telegram delivery/moderation bot (python-telegram-bot)
├── mobile/            Expo / React Native app (the confession booth UI)
├── dashboard/         Admin/config dashboard — Vite + React + shadcn/ui (local dev only)
├── docker-compose.yml Full local stack: db, nats, api, bot, livekit, agent, ollama
├── Dockerfile.api / Dockerfile.agent / bot/Dockerfile
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

### 6. Run the admin dashboard (local dev only)

```bash
cd dashboard && npm install && npm run dev
```

Opens on `http://localhost:5173`. Enter your backend URL and `ADMIN_API_KEY` (from `.env`) in the connection bar. From there you can:

- **Config** — live-edit LLM provider/model, STT model, voice-mask effect chains; changes take effect immediately, no backend restart
- **Status** — ngrok tunnel + self-hosted LiveKit reachability
- **Build** — trigger `gradlew assembleRelease` for the mobile app with a chosen backend URL baked in, and download the resulting APK

### 7. Everyday commands

```bash
make lint          # ruff + mypy (backend/bot) + eslint + tsc (mobile)
make lint-fix       # auto-fix what ruff can
make test           # pytest backend/ bot/ with coverage
make docker-logs     # tail every running container
make clean           # nuke caches, venvs, node_modules, build output
```

Every commit is expected to pass lint + its own tests — see `AGENTS.md` for the full workflow/commit discipline this repo follows.

## ⚙️ Configuration

All configuration is environment-variable driven — `.env.example` (repo root) is the canonical reference, fully commented block-by-block (database, security, Telegram, admin dashboard, LLM provider chain, LiveKit, STT/TTS, observability, CORS/rate-limiting). Copy it to `.env` and never commit real secrets — `.env` is gitignored, `.env.example` must only ever contain placeholders.

A few settings worth knowing about specifically:

- **`LLMService(provider="auto")`** tries Ollama (local/free) → Gemini → OpenAI, first non-empty reply wins. Claude is available but only via explicit `provider="claude"`, never part of the auto chain.
- **LiveKit** defaults (`LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`) match the self-hosted `livekit` Docker service's `--dev` mode (`devkey`/`secret`) — no cloud account needed for local dev.
- **DB-backed live config** — LLM provider/model, `WHISPER_MODEL`, and voice-mask effect chains can be overridden at runtime via the admin dashboard/API without restarting the backend (DB-first, `.env`/`Settings()` as fallback).
- **`ADMIN_API_KEY`** gates every `/api/v1/admin/*` route (`X-Admin-Api-Key` header) — generate a real random value, don't ship the placeholder.

---

*"Auri" — from Latin auricula (ear), auricular confession (whispered in the ear).*
