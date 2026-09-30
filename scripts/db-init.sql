-- ============================================================================
-- Auri Database — Initialisation Script
-- ============================================================================
-- This script runs automatically when the PostgreSQL container starts for the
-- first time (placed in /docker-entrypoint-initdb.d/).
-- ============================================================================

-- pgcrypto is installed but no column is encrypted with it today
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
-- Application tables are owned by Alembic (backend/alembic/versions/) and
-- created via `alembic upgrade head`, not here. This script previously
-- defined its own copy of the schema (confessions/moderation_queue/
-- delivery_log with different columns than the Alembic models), which raced
-- Alembic on a fresh database: docker-entrypoint-initdb.d created the tables
-- first, then `alembic upgrade head` failed with
-- `DuplicateTableError: relation "confessions" already exists`.
