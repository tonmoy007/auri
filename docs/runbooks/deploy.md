# Deploy runbook

## What triggers a deploy

`.github/workflows/deploy.yml` runs when the **CI** workflow completes on `main`.
It does nothing unless that CI run passed and was a push (a merge), and it builds
the exact commit CI tested (`workflow_run.head_sha`), not whatever `main` points
at by then. Deploys are serialised (`concurrency: deploy-production`), so two
merges in quick succession deploy one after the other.

Steps:

1. **Build & Push** builds the API and bot images and pushes them to GHCR tagged
   with the tested commit's short SHA and `main`.
2. **Deploy** (only when the repository variable `DEPLOY_ENABLED` is `true`)
   connects over SSH, runs `docker compose pull` and `docker compose up -d` in
   `/opt/auri`, then fails the run if `https://<DEPLOY_HOST>/health` does not
   answer within the check.
3. **Notify** (only when `TELEGRAM_NOTIFY_ENABLED` is `true`) posts the result.

A failed CI run on `main` builds nothing and deploys nothing.

## When the health check fails

The new containers are already running when the check fails. Roll back:

1. **Preferred:** revert the merge commit on `main` (`git revert <merge-commit>`,
   through a pull request as usual). CI runs on the revert and, once green, the
   workflow deploys it. This keeps `main` and production in step (`AGENTS.md` 11.2).
2. **Faster, while the revert is in review:** on the server, point the compose
   file's image tags at the previous commit's short SHA (every deployed commit
   has its own tag in GHCR), then `docker compose pull && docker compose up -d`.
   Undo this once the revert has deployed, or the next deploy's `main` tag and the
   pinned tag will disagree.

## Re-running a deploy

Re-run the **Deploy** workflow run from the Actions tab. It rebuilds the same
tested commit; it does not pick up newer commits.
