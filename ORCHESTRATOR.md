# Auri — Orchestrator Rules

> **Purpose**: This file governs how AI agents plan, sequence, and track work in this repo. `AGENTS.md` defers to this file for all plan/task/phase discipline. Every agent (Claude Code, Cursor, Copilot, etc.) must follow it.

---

## 1. Source of Truth

- Active plan lives in `.hermes/plans/<timestamp>-auri-plan.md`.
- Task/phase progress lives in `.hermes/plans/tracking.json`.
- These two files are the **only** source of truth for what phase/task is active, pending, or done. Do not track progress in memory, chat, or scratch notes only — write it to disk.

## 2. Phase & Task Structure

- Work is organized into **phases** (`1`, `2`, `3`, ...), each phase holding **tasks** (`1.1`, `1.2`, ...).
- Each task status is one of: `pending`, `in_progress`, `completed`, `blocked`.
- Each phase status is one of: `pending`, `in_progress`, `completed`.
- A phase moves to `completed` only when every task inside it is `completed`.

## 3. Required Updates to `tracking.json`

Update `tracking.json` immediately (same turn, before moving to the next task) whenever:

- **A task starts** — set its status to `in_progress`.
- **A task completes** — set its status to `completed`.
- **A new task is added** — append it under the correct phase's `tasks` object with status `pending`, and reflect it in the plan file's task list too.
- **A new phase is added** — append it under `phases` with status `pending` and its own `tasks` object; document the phase in the plan file.
- **A phase completes** — set its status to `completed` and update `last_phase`.

Always bump `updated_at` to the current timestamp on every write.

## 4. Required Updates to the Plan File

The plan markdown file (`.hermes/plans/<timestamp>-auri-plan.md`) must stay in sync with `tracking.json`:

- New phases get a new `## Phase N — <name>` section.
- New tasks get a new checklist line under their phase.
- Completed tasks get checked off (`- [x]`) in place, not deleted.
- Do not rewrite history — append and mark, don't silently remove prior phases/tasks.

## 5. Workflow Loop

1. Read `tracking.json` to find the current phase/task.
2. Mark the task `in_progress`.
3. Do the work (respecting `AGENTS.md` rules — atomic tasks, one commit per task, tests, etc.).
4. Mark the task `completed` in both `tracking.json` and the plan file.
5. If the task uncovered new work, add it as a new task (or new phase) in both files before continuing.
6. Move to the next `pending` task in the current phase; if none remain, mark the phase `completed` and move to the next phase.

## 6. Guardrails

- Never mark a task `completed` without the corresponding commit existing (see `AGENTS.md` §1.2–1.3).
- Never delete a phase or task from either file — mark it `blocked` or superseded instead, so history stays intact.
- If `tracking.json` and the plan file ever disagree, `tracking.json` is authoritative for status; the plan file is authoritative for scope/description. Reconcile immediately, don't proceed until they match.

---

*Last updated: 2026-07-17*
