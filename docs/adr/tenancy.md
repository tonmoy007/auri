# ADR: One company per deployment

- **Status:** accepted, 2026-10-01 (owner decision, plan task 15.9).

## Decision

One Auri deployment serves one organisation. A second organisation gets its own deployment: its own backend, database, dashboard, bot token and secrets. There is no `tenant_id` column and no tenant filter in any query.

## Why

- **Anonymity is easier to keep with nothing shared.** A confession, an HR note or an audit row cannot leak to another company through a missing `WHERE tenant_id = ...` if no other company is in the database. The cohort rule (`ANALYTICS_MIN_COHORT`) also stays simple: every figure is about one organisation's people.
- **The retrofit cost is known and bounded only while there is one tenant.** Multi-tenancy would put `tenant_id` on `users`, `departments`, `confessions`, `audit_events`, `anonymous_users`, `retention_runs` and `app_settings` (and `insight_daily_counts` once plan 14.12 lands), into every query and unique constraint, into the role checks, and into the retention job, metrics and config layer. Doing that now would be speculative work on every table.
- **The settings model is already per deployment.** Retention windows, crisis contacts, the Guide's kill switch and model addresses, and the Telegram target are company policy and live in one `Settings` plus the `app_settings` layer.

## Consequences

- Running several companies means one stack per company. Plan for it in the deployment tooling (one compose project or namespace each), not in the code.
- The mobile app talks to one backend at a time: the build-time `EXPO_PUBLIC_API_URL`, or the runtime override set in the app's Settings. So one app build can already be pointed at a company's own backend; a friendlier company picker is a product decision for later and needs no schema change.
- Cross-company reporting is not possible from one database, and is not wanted: it would pool anonymous data across employers.

## When to revisit

If Auri is to be offered as a hosted service with many small customers, where one stack each is too costly. That would be a new ADR and its own phase: a `tenant_id` on every table above, row-level security in Postgres as a second guard, and tests that prove no query crosses tenants.
