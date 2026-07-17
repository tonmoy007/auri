# Auri — Development Guidelines & Agent Rules

> **Purpose**: This file is read by AI coding agents (Claude Code, Cursor, Copilot, etc.)
> at session start. Every rule must be followed unless explicitly overridden by the user.

> **Orchestration**: Follow `ORCHESTRATOR.md` for plan/task/phase discipline. It governs how work is tracked in `.hermes/plans/` — update the plan file and `tracking.json` any time a task completes, a task is added, or a phase is added/completed.

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
- Every commit **must pass lint and type checks** — `ruff check backend/ bot/ && mypy backend/ bot/ --ignore-missing-imports` for Python; `npm run typecheck` in `mobile/` for the Expo app.
- Every commit **must pass its own tests** — `pytest <changed-path>` for Python; `npx vitest related <changed-path>` in `mobile/` for the Expo app.
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
- **Trunk-based flow**: this repo currently has only `main`. Only merge via pull request. Never commit directly. Protected.
- **Feature branches** branch from `main`, PR back to `main`.
- Delete branches after merge (`git branch -d`).
- A `develop` integration branch is **optional** and should only be introduced once the team is large enough that direct-to-`main` PRs cause contention (see §11 for how CI would need to change if adopted).

### 2.3 Before Branching
```bash
git checkout main
git pull --rebase origin main
git checkout -b feat/my-feature
```

### 2.4 During Development
- Rebase onto `main` daily to avoid long-lived divergence:
  ```bash
  git fetch origin main
  git rebase origin/main
  ```
- Force-push is allowed ONLY on feature branches (never on `main`).

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
- Use **squash merge** to keep `main` history clean.

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
Defined in `.github/workflows/ci.yml`, four parallel jobs:

| Job | Stage | Command | Required |
|---|---|---|---|
| Lint | Python lint | `ruff check backend/ bot/` | ✅ |
| Lint | Python format check | `ruff format --check backend/ bot/` | ✅ |
| Lint | Python type check | `mypy backend/ bot/ --ignore-missing-imports` | ✅ |
| Lint | Mobile lint | `npx eslint . --ext .ts,.tsx` (in `mobile/`) | ✅ |
| Lint | Mobile type check | `npx tsc --noEmit` (in `mobile/`) | ✅ |
| Test | Python tests | `pytest backend/ bot/ --cov=backend --cov=bot` | ✅ |
| Test | Mobile tests | `npx vitest --coverage` (in `mobile/`) | ✅ |
| Build | Docker image build | `docker compose build` | ✅ |
| Security | Trivy filesystem scan (HIGH/CRITICAL) | — | ✅ |

### 5.4 Pre-commit Hook (via the `pre-commit` framework, `.pre-commit-config.yaml`)
- `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`, `detect-private-key`, `check-merge-conflict`, `check-toml`
- `ruff --fix` and `ruff-format` on staged Python files
- `mypy --ignore-missing-imports` on `backend/` and `bot/`
- `prettier` on staged JS/TS/JSON/CSS/Markdown/YAML files
- `eslint` on staged `mobile/**/*.{ts,tsx}` files

---

## 6. Pixel-Perfect Responsive UI

### 6.1 Design Constraints
The Auri frontend is **Expo / React Native**, not a web app — most of this section applies conditionally, noted per rule.
- **Mobile-first** breakpoints: 320px, 375px, 414px, 768px, 1024px, 1440px. Applies only if a web build (`expo start --web` target or a separate web app) ships; React Native layout uses `Dimensions`/flexbox breakpoints instead of CSS media queries.
- Every component must render correctly on all breakpoints — no horizontal scroll. *(Web-only rule — see above.)*
- **If a web app is added**: use relative CSS units (`rem`, `em`, `vh`, `vw`, `%`); never use `px` for spacing/margins.
- **In React Native code** (current `mobile/` app): use density-independent numbers (React Native's default unit — not CSS `px`) for `StyleSheet` values; there is no `rem`/`em` equivalent in RN.
- Text must be readable at **200% browser zoom** without truncation. *(Web-only rule — see above.)*
- Touch targets: **minimum 44×44 dp** (iOS HIG) / **48×48 dp** (Material Design) — applies to both RN and any web surface.

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
- **No secrets** in code. Use env vars referenced via `os.getenv()`. See §12 for the full policy and CI enforcement pattern.
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
- Terminal commands must use development env files (`.env.development`) — never prod credentials.
- API calls to LLM endpoints must include a `request_id` for tracing.
- MCP servers are configured via the AI agent's own settings (e.g. `.claude/settings.json`) — do not install them at project level.
- Use `npx`/`uvx` for MCP servers; never pin them in devDependencies.

### 8.4 Prompt Engineering Rules
- All LLM prompts live in `backend/app/llm/prompts/` as `.md` files with frontmatter. *(Create this directory when the first prompt lands — it does not exist yet.)*
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
- All model names, endpoints, and parameters in `backend/config/` YAML files. Never hardcoded. *(Create this directory when the first config file lands — it does not exist yet.)*
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
Currently implemented in `.github/workflows/ci.yml` (see §5.3): `lint`, `test`, `build`, and `security` jobs run on every push and on PRs to `main`. The deploy stages below are **aspirational** — no deploy job exists in `ci.yml` yet.

| Trigger | Workflow | Status |
|---|---|---|
| Push to any branch | Lint + type check + unit/integration tests (§5.3) | ✅ implemented |
| PR to `main` | Full test suite + Docker build + security scan | ✅ implemented |
| Merge to `main` | E2E tests + deploy production + tag release | ⏳ not yet implemented |

### 11.2 Deployment
- Production: auto-deployed on merge to `main`. *(Not yet implemented — no deploy job in `ci.yml`.)*
- Rollback: `git revert <merge-commit>` and push.
- Zero-downtime deploys via blue-green.
- Health check endpoint (`/health`) must pass before traffic routes.

---

## 12. Zero Secrets Policy (Canonical — §7.1 and §15.4 cross-reference this section)

Every config variable **must** be environment-bound. **Zero exceptions.**

| Rule | Enforcement |
|---|---|
| No hardcoded API keys, tokens, passwords, or credentials in any file | `detect-private-key` pre-commit hook + manual review fail |
| No test tokens, sandbox keys, or dev secrets in code | `.env.example` uses placeholder values only (`your_key_here`, `<secret>`) |
| No `.env` files committed | `.env*` in `.gitignore` except `.env.example` |
| Secrets in CI via GitHub Secrets, never in workflow YAML | `${{ secrets.* }}` only — no inline values |
| Fail the build/PR if any secret pattern is detected | CI job scans changed files for **literal-value assignments**: `(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['"][^'"]{8,}['"]` (a quoted literal of 8+ chars, not a bare identifier). Excludes `.env.example`, `*.md`, and `docs/**`, so placeholder values and field/parameter names alone (`bot_token: str`, `device_token_hash`) don't false-positive. |

Every engineer and every AI agent must fail immediately if they see hardcoded credentials — **do not ship, do not commit, flag it.**

---

## 13. Structural & Complexity Guardrails

### 13.1 Max Function Lines: 40
- **Every function must be ≤40 lines** (including signature, docstring, body).
- If a function exceeds 40 lines, refactor into smaller named helpers.
- Exception: data-heavy mapper functions (max 50 lines).

### 13.2 Max File Size: 400 Lines
- **Every file must be ≤400 lines** of actual code (comments/docs don't count).
- Files exceeding 400 lines must be split into focused modules.
- Exception: auto-generated files (migrations, lock files, schemas).

### 13.3 Max Indentation Depth: 3 Levels
- **Never nest deeper than 3 levels** of indentation.
- ```
  ❌ function() {       // level 1
  ❌   for (...) {       // level 2
  ❌     if (...) {      // level 3
  ❌       try {         // level 4 — VIOLATION
  ```
- Solution: extract inner logic to a named function, use guard clauses.

### 13.4 Guard Clauses & Early Returns
- Handle **errors, edge cases, and invalid inputs first** — before the core logic.
- Keep the **happy path (primary workflow) unindented at the bottom** of the function.
- ```python
  # ✅ GOOD
  def process_confession(raw: str) -> Confession:
      if not raw or not raw.strip():
          raise ValueError("empty confession text")
      if detect_pii(raw):
          raw = strip_pii(raw)
      sanitized = sanitize_text(raw)        # core logic — flush left
      return Confession(text=sanitized)

  # ❌ BAD
  def process_confession(raw: str) -> Confession:
      if raw and raw.strip():                # main logic inside if
          s = sanitize_text(raw)
          return Confession(text=s)
      else:
          raise ValueError("empty")          # error at the end
  ```

---

## 14. Naming & Anti-Laziness Standards

### 14.1 Banned Generic Names
The following terms are **banned** in file names, class names, function names, and module directories:

| Banned | Instead Use |
|---|---|
| `helpers` | `formatters`, `validators`, `parsers`, `transformers` |
| `utils` | `sanitizer`, `hasher`, `normalizer`, `date_utils` (only if scoped) |
| `data` | `payload`, `record`, `entity`, `dto`, `schema` |
| `manager` | `orchestrator`, `coordinator`, `handler`, `service`, `controller` |
| `common` | `shared`, `base`, `core` or nothing — put it in the domain module |
| `misc` | Name the actual category |
| `stuff` / `things` | Never acceptable |

### 14.2 Descriptive Naming
- A name must describe **what** the thing does, not **how**.
- `fetch_authenticated_user()` ✅ — `get_data()` ❌
- `ConfessionProcessor` ✅ — `DataManager` ❌
- `sanitize_html()` ✅ — `clean_input()` ❌ (clean what?)

### 14.3 No Placeholders
- `// TODO: implement later` — **not allowed** in committed code.
- `# FIXME` — only if a GitHub issue number is attached: `# FIXME(#123)`.
- `// Implement this` — never. Write the full implementation or don't merge.
- If a function is genuinely deferred, gate it behind a feature flag and raise `NotImplementedError` with tracking issue.

### 14.4 Explicit Parameters
- **Never** use generic `**kwargs`, `dict[str, Any]`, or `data` bundle for function inputs.
- Every parameter must be explicitly named and typed.
- ```python
  # ✅ GOOD
  def create_confession(text: str, voice_mask: VoiceMask, device_token: str) -> Confession: ...

  # ❌ BAD
  def create_confession(data: dict, opts: dict[str, Any]) -> Any: ...
  ```

---

## 15. Error Handling & Security Guardrails

### 15.1 No Swallowed Exceptions
- **Bare `except:`** — banned.
- **`except Exception:` without re-raise or log** — banned.
- Every exception handler must do one of:
  - Log + raise a domain-specific exception
  - Log + return a safe fallback (graceful degradation)
  - Log + retry with backoff
- ```python
  # ✅ GOOD
  try:
      result = await llm_service.deidentify(text)
  except LLMServiceError as e:
      logger.error("deidentification failed", request_id=request_id, error=str(e))
      raise ProcessingError("deidentification failed, confession quarantined") from e

  # ❌ BAD
  try:
      result = await llm_service.deidentify(text)
  except:
      pass
  ```

### 15.2 Custom Exception Hierarchy
- Every domain layer defines its own exception types.
- ```
  AuriError (base)
   ├── ProcessingError
   │    ├── DeidentificationError
   │    ├── CategorizationError
   │    └── SummarizationError
   ├── DatabaseError
   │    ├── ConfessionNotFoundError
   │    └── DuplicateConfessionError
   ├── ServiceError
   │    ├── STTError
   │    ├── TTSError
   │    └── VoiceModulationError
   └── ValidationError
        ├── EmptyConfessionError
        └── RateLimitError
  ```
- Catch specific types — never `except Exception`.
- All custom exceptions inherit from `AuriError` which extends `Exception`.

### 15.3 Input Validation at Entry
- Every API endpoint, bot command, and service boundary validates input **at the entry point**.
- FastAPI: use Pydantic models for request bodies (already Section 7.3).
- Telegram bot: validate `update` payload before processing.
- Internal services: validate with `pydantic.TypeAdapter` or `dataclass` frozen validation.
- **Reject early, reject loudly** — return 400 with structured error message.

### 15.4 Zero Secrets (Reinforced)
- Also fail CI if any committed file contains a private key block (`BEGIN RSA` / `BEGIN OPENSSH PRIVATE KEY`, etc.) — the general secret-literal scan pattern is defined once, in §12.
- Use `pre-commit` hook `detect-private-key` as mandatory gate.
- `.env` files are loaded via `python-dotenv` and never committed. See §12.

---

## 16. Testing Verification Mandates

### 16.1 AAA (Arrange-Act-Assert) Structure
Every test **must** follow this exact structure, separated by blank lines:

```python
# ✅ GOOD
def test_deidentify_strips_email() -> None:
    # Arrange
    text = "My email is user@example.com"
    service = DeidentifyService()
    expected = "My email is [redacted]"

    # Act
    result = service.deidentify(text)

    # Assert
    assert result.text == expected
    assert result.pii_found == 1
```

- **Arrange** — Set up inputs, mocks, preconditions. Only code between `# Arrange` and `# Act`.
- **Act** — Execute the single behavior under test. Only code between `# Act` and `# Assert`.
- **Assert** — Verify results with specific assertions. Only code after `# Assert`.
- **No mixing**: Act code must not contain setup logic. Assert must not modify state.

### 16.2 No Generic Asserts
- **`assert True` / `assert False` / `assertTrue()` / `assertFalse()`** — banned.
- Use specific assertions:
  | Instead of | Use |
  |---|---|
  | `assert result is not None` | `assert result.field == expected_value` |
  | `assert len(items) > 0` | `assert items[0].status == ConfessionStatus.PENDING` |
  | `assertTrue(user.is_active)` | `assert user.status == UserStatus.ACTIVE` |
  | `assertEqual(a, b)` | `assert a.field == b.field` |
- Every assertion must check a **meaningful business outcome**, not a tautology.

### 16.3 Zero Test Interdependencies
- **Tests must not share state.** No global variables, no ordered test suites, no `@pytest.mark.dependency`.
- Each test creates its own fixtures (or uses `pytest.fixture` with `scope="function"`).
- Running a single test in isolation must produce the same result as running the full suite.
- Use `pytest-randomly` to detect hidden ordering dependencies.
- Database tests: each test creates and destroys its own data (rollback or truncate between tests).

### 16.4 Mock External Boundaries — Never Core Domain
- **Mock**: HTTP calls, databases, filesystem, third-party APIs, time/clocks.
- **Never mock**: business rules, validation logic, de-identification algorithms, category assignment.
- ```python
  # ✅ GOOD — mock network boundary
  with patch("app.services.stt.WhisperTranscriber._call_api") as mock_api:
      mock_api.return_value = "transcribed text"
      result = transcriber.transcribe("audio.wav")

  # ❌ BAD — mock domain logic
  with patch("app.services.deidentify.DeidentifyService._strip_emails") as mock_strip:
      mock_strip.return_value = "text without email"
      # This test proves nothing about de-identification
  ```

### 16.5 Deterministic Tests — Freeze Time & Inject Clocks
- **Never use `datetime.now()`, `Date.now()`, `time.time()`, or `timezone.localtime()` directly in testable code.**
- Inject a clock or time provider:
  ```python
  # ✅ GOOD
  class ConfessionService:
      def __init__(self, clock: Callable[[], datetime] = datetime.now):
          self._now = clock

  def test_expired_confession_is_auto_deleted() -> None:
      # Arrange
      fixed_time = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
      service = ConfessionService(clock=lambda: fixed_time)
      ...
  ```
- For TypeScript/JavaScript, use `vi.useFakeTimers()` (Vitest) or `jest.useFakeTimers()`.
- For Python, use `freezegun` / `time-machine` libraries, or pass an explicit clock.
- UUID generation must use seeded/testable generators in tests.

### 16.6 Test Size Caps
- Maximum **20 assertions per test**.
- Maximum **80 lines per test function** (including AAA comments).
- Maximum **3 mock patches per test** — if you need more, refactor the code under test.

---

*Last updated: 2026-07-17*
*This file is reviewed every sprint and updated as practices evolve.*
