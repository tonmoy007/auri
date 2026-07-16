# Auri — Development Guidelines & Agent Rules

> **Purpose**: This file is read by AI coding agents (Claude Code, Cursor, Copilot, etc.)
> at session start. Every rule must be followed unless explicitly overridden by the user.

---

## 1. Workflow: Small Tasks, Frequent Commits

### 1.1 Task Decomposition
- Break every feature into **atomic tasks** — each task should be completable in a single coding session (<2h).
- Each task maps to exactly **one commit**.
- If a task feels "too big," split it further. A task that takes >4h is not atomic.

### 1.2 Commit Discipline

```
<type>(<scope>): <imperative-verb> <short-description>

[optional body — motivation, trade-offs, what changed]
```

**Types**: `feat`, `fix`, `refactor`, `test`, `docs`, `style`, `perf`, `chore`, `ci`, `revert`
**Scope**: `api`, `web`, `mobile`, `bot`, `db`, `infra`, `ui`, `llm`, `config`

**Rules**:
- Every commit **must build** (`npm run build || true`).
- Every commit **must pass its own tests** (`npm run test:related`).
- No `--no-verify` unless upstream CI is broken and you're unblocking yourself.
- No "WIP" or "temp" commits. If you must checkpoint, use `git stash` or a patch file.
- ✅ `feat(ui): add confession booth 3D scene with environment cycling`
- ❌ `fix stuff`
- ❌ `wip`

### 1.3 Commit per Logical Change
- One commit = one logical change. Not "refactor + fix + feature" in one commit.
- Use `git add -p` to stage only the relevant hunks.

---

## 2. Branching Strategy

### 2.1 Branch Naming
```
<type>/<short-kebab-description>
```

Examples: `feat/confession-3d-scene`, `fix/ios-layout-bleed`, `refactor/llm-prompt-chain`

### 2.2 Branch Rules
- **`main`** is production-ready. Only merge via pull request. Never commit directly. Protected.
- **`develop`** is integration branch. Feature branches merge here for testing. Protected.
- **Feature branches** branch from `develop`, PR back to `develop`.
- **Release branches** cut from `develop` → `main` when green.
- Delete branches after merge (`git branch -d`).

### 2.3 Before Branching
```bash
git checkout develop
git pull --rebase origin develop
git checkout -b feat/my-feature
```

### 2.4 During Development
- Rebase onto `develop` daily to avoid long-lived divergence:
  ```bash
  git fetch origin develop
  git rebase origin/develop
  ```
- Force-push is allowed ONLY on feature branches (never on `main` or `develop`).

---

## 3. Pull Request Discipline

### 3.1 PR Template

```markdown
## What
<1-2 sentences describing the change>

## Why
<motivation, user impact>

## Test Plan
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual test on <device/browser>
- [ ] Screenshots (for UI changes)

## Checklist
- [ ] Self-reviewed code
- [ ] No lint errors
- [ ] No secrets committed
- [ ] Missing translations addressed
- [ ] Mobile-responsive verified
```

### 3.2 PR Size
- **<200 lines changed** ideal. **<400 lines** max. Larger PRs must be split.
- No PR should touch more than 3 files in unrelated areas.
- UI-only PRs: include screenshots or screen recording.

### 3.3 Review Rules
- Every PR must have at least **one approving review** before merge.
- No self-approvals.
- Reviewer must **run the code** (or verify CI passed) before approving.
- Comments must be addressed — explicitly resolve or justify "won't fix."
- **48h max turnaround** for reviews. If unreviewed, ping after 24h.
- Use **squash merge** to keep `develop` history clean. Use **merge commit** only for `develop → main`.

---

## 4. Main Branch Protection

Configured via GitHub branch protection rules:

| Rule | Value |
|---|---|
| Require PR | ✅ |
| Require approvals | **1** |
| Dismiss stale reviews | ✅ |
| Require status checks | ✅ (CI must pass) |
| Require branches up-to-date | ✅ |
| Require signed commits | Optional |
| Include administrators | ✅ |
| Lock branch | ❌ |
| Allow force pushes | ❌ |
| Allow deletions | ❌ |

---

## 5. Rigorous Testing

### 5.1 Test Pyramid
```
    ╱╲
   ╱ e2e ╲        ← Playwright/Cypress (happy path + critical flows)
  ╱────────╲
 ╱ integration ╲   ← pytest + FastAPI TestClient (API, DB, LLM)
╱────────────────╲
╱   unit tests    ╲  ← pytest, vitest (every function, every component)
╱──────────────────╲
```

### 5.2 Test Rules
- **RED-GREEN-REFACTOR**: Write failing test first, make it pass, then clean up.
- **100% coverage** on: LLM prompt logic, de-identification, database constraints, API input validation.
- **Every bug fix** must add a regression test first (prove it fails, then fix).
- Mock external services (LLM APIs, Telegram, Spotify) in unit tests; use real instances in integration.
- **Flaky tests** must be quarantined or fixed within 24h.

### 5.3 CI Pipeline
| Stage | Command | Required |
|---|---|---|
| Lint | `npm run lint` | ✅ |
| Type check | `npm run typecheck` | ✅ |
| Unit tests | `npm run test:unit` | ✅ |
| Integration tests | `npm run test:integration` | ✅ |
| Build | `npm run build` | ✅ |
| E2E (on merge to develop) | `npm run test:e2e` | ✅ |

### 5.4 Pre-commit Hook (via husky + lint-staged)
- `eslint --fix` on staged files
- `prettier --check` on staged files
- TypeScript type check on changed files
- Quick unit test for changed modules

---

## 6. Pixel-Perfect Responsive UI

### 6.1 Design Constraints
- **Mobile-first** breakpoints: 320px, 375px, 414px, 768px, 1024px, 1440px.
- Every component must render correctly on all breakpoints — no horizontal scroll.
- Use **relative units** (`rem`, `em`, `vh`, `vw`, `%`). Never use `px` for spacing/margins.
- Text must be readable at **200% browser zoom** without truncation.
- Touch targets: **minimum 44×44px** (iOS HIG) / **48×48px** (Material Design).

### 6.2 Device Support — Tier System

| Tier | Devices | Target |
|---|---|---|
| **S** | iPhone SE, Galaxy S10e, Pixel 4a | Pixel-perfect |
| **M** | iPhone 14/15/16, Galaxy S23/24, Pixel 7 | Pixel-perfect |
| **L** | iPad mini, iPad Pro, Galaxy Tab | Pixel-perfect |
| **XL** | 13"–16" laptops | Pixel-perfect |
| **XXL** | 24"–32" desktops | Pixel-perfect |

Test matrix: 5 devices per tier, covering **iOS Safari**, **Chrome Android**, **Firefox Desktop**, **Safari macOS**.

### 6.3 Three.js (React Three Fiber) UI Rules
- `useFrame` must check `performance.now()` delta — skip frames below 30 FPS.
- Mobile: auto-downgrade post-processing, disable shadows, reduce polygon count.
- Fall back gracefully to a 2D scene if WebGL fails.
- Loading spinner while 3D assets load. Never show a blank screen.

---

## 7. Code Quality & Maintainability

### 7.1 General Rules
- **No magic numbers** or strings. Extract to constants/enums.
- **No `any`** in TypeScript. Use `unknown` and narrow with type guards.
- **No `console.log`** in committed code. Use a proper logger.
- **No dead code**. Remove commented-out blocks. Git history has everything.
- **No secrets** in code. Use env vars referenced via `os.getenv()`.
- **No circular dependencies**. Use `madge` or `dpdm` to detect them.

### 7.2 Component Architecture
- One component = one file.
- Split presentational (stateless) from container (stateful) layers.
- Use `React.forwardRef` for reusable primitives.
- Every `useEffect` must have a cleanup function (unless truly empty mount).

### 7.3 API Design (FastAPI)
- Pydantic models for every request/response. No raw dict returns.
- All endpoints versioned: `/api/v1/...`.
- Idempotency keys for mutation endpoints.
- Rate limiting with configurable tiers.
- Structured logging with `structlog`.

### 7.4 Database
- All schema migrations via Alembic. Never raw SQL.
- Queries must use parameterized statements (SQL injection prevention).
- Index every foreign key and every column in WHERE/JOIN/ORDER BY.
- `SELECT *` is banned — always name columns.

---

## 8. Agentic Development Rules

### 8.1 Session Discipline
- AI agent reads **AGENTS.md** at session start (this file).
- Before writing code, read existing files in the module to understand conventions.
- **Do not** delete or significantly restructure code you didn't write unless explicitly asked.
- **Do not** introduce new dependencies without documenting why and getting approval.

### 8.2 Agent Communication
- When exploring code, prefer `search_files`/`read_file` over `terminal` cat/grep.
- When generating code, prefer small incremental writes over one giant file.
- After each commit, verify with a build + test run.
- If stuck on a bug >15min, stop and explain: the human may see the issue faster.

### 8.3 Tool Use
- Terminal commands must use pat envy (`.env.development`) — never prod credentials.
- API calls to LLM endpoints must include a `request_id` for tracing.
- MCP servers are loaded in Hermes config — do not install them at project level.
- Use `npx`/`uvx` for MCP servers; never pin them in devDependencies.

### 8.4 Prompt Engineering Rules
- All LLM prompts live in `src/llm/prompts/` as `.md` files with frontmatter.
- Every prompt has a version comment (`# v1.2`).
- Prompt templates use `{mustache}` syntax — never f-string interpolation in templates.
- System prompts must include output format constraints (JSON schema / markdown structure).
- De-identification prompt runs BEFORE categorization prompt — chain sequential.

### 8.5 Safety & Guardrails
- No user input is concatenated directly into LLM prompts — strip/escape first.
- Confession content: de-identify BEFORE storing or sending to LLM.
- Rate limit confession submissions to 1 per 5min per user.
- Always log LLM calls with anonymized request ID for audit.

---

## 9. LLM & AI Specific

### 9.1 Model Configuration
- All model names, endpoints, and parameters in `config/` YAML files. Never hardcoded.
- Fallback chain: primary → secondary → failover → error response.
- Retry with exponential backoff (3 attempts, 1s → 2s → 4s).
- Timeout: 30s for simple tasks, 120s for generation.

### 9.2 Prompt Versioning
- Every prompt change = new Git commit with `docs(prompt): ...`
- Prompt updates must include the diff in the PR description.
- A/B test prompt changes when possible (split 50/50 for 100 samples).

---

## 10. Documentation

### 10.1 In-Code
- Docstrings for every public function/method (Google style for Python, TSDoc for TypeScript).
- `README.md` per major module explaining purpose, key types, and usage.
- Architecture Decision Records (ADRs) in `docs/adr/` for significant decisions.

### 10.2 UI Documentation
- Component library documented with Storybook stories.
- Every story covers: default state, loading, error, empty, edge cases.
- Visual regression tests via Chromatic or Percy.

---

## 11. CI/CD

### 11.1 GitHub Actions
| Trigger | Workflow |
|---|---|
| Push to feature branch | Lint + typecheck + unit tests |
| PR to develop | Full test suite + build + preview deploy |
| Merge to develop | Integration tests + deploy staging |
| Merge to main | E2E tests + deploy production + tag release |

### 11.2 Deployment
- Staging: auto-deployed on merge to `develop`.
- Production: auto-deployed on merge to `main`.
- Rollback: `git revert <merge-commit>` and push.
- Zero-downtime deploys via blue-green.
- Health check endpoint (`/health`) must pass before traffic routes.

---

*Last updated: 2026-07-17*
*This file is reviewed every sprint and updated as practices evolve.*
