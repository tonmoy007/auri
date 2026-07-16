# 🕯️ Auri

**Whisper in the ear.** An anonymous AI-driven confession booth for internal teams.

Speak your truth in a candlelit 3D booth. AI listens, processes, and lets you forward anonymously or delete. Your voice is masked. Your identity never stored.

## ✨ Features

- **Immersive 3D Booth** — Interactive candlelit confessional built with React Three Fiber
- **AI STT/TTS Agent** — Whisper transcription + Edge-TTS voice response
- **Voice Modulation** — 5 voice masks (Warm, Robotic, Ethereal, Deep, Random) via SoX + RVC
- **Anonymity Modes** — Fully blind or "someone in your team" context — your choice at send-time
- **Telegram Delivery** — Confessions delivered via anonymous Telegram bot DM
- **Moderation** — AI-flagged content queued to designated moderator for review
- **Forward or Delete** — Send to department/person or extinguish forever
- **3 Environments** — Classic booth, forest glade, rooftop at night

## 🏗️ Architecture

| Layer | Stack |
|-------|-------|
| Mobile | React Native + Expo |
| 3D UI | React Three Fiber + drei + Three.js |
| Backend | FastAPI + WebSocket |
| STT | OpenAI Whisper / faster-whisper |
| TTS | Edge-TTS |
| Voice Mod | SoX pitch/formant + RVC AI voice conversion |
| AI Agent | GPT-4o / Claude |
| DB | PostgreSQL + pgcrypto |
| Delivery | Telegram Bot (python-telegram-bot) |

## 📱 The Flow

```
[Enter Auri] → Pick Voice Mask → AI greets you
    ↓
[Speak] → Voice modulated in real-time → STT transcribes
    ↓
[AI processes] → Strips PII, categorizes, summarizes
    ↓
[Review] → Transcription | AI Summary | Voice-masked Audio
    ↓
[Choose] → Fully blind / "Someone in your team"
    ↓
[Act] → Send anonymously | Forward to person | Delete
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

- No user accounts — anonymous device tokens only
- Audio deleted from server after transcription
- All PII stripped by LLM before storage
- Original voice never stored — only the masked version
- Encrypted at rest with pgcrypto
- Blind relay — recipient never knows who sent it

## 🚀 Getting Started

*Coming soon — Phase 1 onwards.*

---

*"Auri" — from Latin auricula (ear), auricular confession (whispered in the ear).*
