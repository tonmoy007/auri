-- ============================================================================
-- Auri Database — Initialisation Script
-- ============================================================================
-- This script runs automatically when the PostgreSQL container starts for the
-- first time (placed in /docker-entrypoint-initdb.d/).
-- ============================================================================

-- Enable pgcrypto extension for encrypted-at-rest storage
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Create the application database (idempotent — only created if missing)
SELECT 'CREATE DATABASE auri'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'auri')\gexec

-- Connect to the auri database (this runs as a separate command)
\c auri

-- Enable pgcrypto on the application database too
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================================
-- Core Schema
-- ============================================================================

-- Confessions table
CREATE TABLE IF NOT EXISTS confessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_token    TEXT NOT NULL,                     -- anonymous device fingerprint
    content         TEXT NOT NULL,                     -- de-identified confession text
    content_hash    TEXT NOT NULL,                     -- SHA-256 for dedup
    voice_mask      TEXT NOT NULL DEFAULT 'warm',      -- warm, robotic, ethereal, deep, random
    category        TEXT,                              -- AI-assigned category
    summary         TEXT,                              -- LLM-generated summary
    recipient       TEXT,                              -- department or person (null = anonymous)
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending, forwarded, deleted, flagged
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    forwarded_at    TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ,
    moderated_by    TEXT,                              -- moderator who reviewed (if flagged)
    moderated_at    TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_confessions_device_token ON confessions (device_token);
CREATE INDEX IF NOT EXISTS idx_confessions_status ON confessions (status);
CREATE INDEX IF NOT EXISTS idx_confessions_created_at ON confessions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_confessions_content_hash ON confessions (content_hash);

-- Moderation queue
CREATE TABLE IF NOT EXISTS moderation_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    confession_id   UUID NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
    reason          TEXT NOT NULL,                     -- why it was flagged
    flagged_by      TEXT NOT NULL DEFAULT 'ai',        -- 'ai' or 'user'
    reviewed        BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_moderation_queue_reviewed ON moderation_queue (reviewed);
CREATE INDEX IF NOT EXISTS idx_moderation_queue_confession_id ON moderation_queue (confession_id);

-- Delivery log (for observability, no PII)
CREATE TABLE IF NOT EXISTS delivery_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    confession_id   UUID NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
    telegram_chat_id BIGINT,
    status          TEXT NOT NULL DEFAULT 'sent',      -- sent, failed, bounced
    error_message   TEXT,
    delivered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_delivery_log_confession_id ON delivery_log (confession_id);
CREATE INDEX IF NOT EXISTS idx_delivery_log_delivered_at ON delivery_log (delivered_at DESC);
