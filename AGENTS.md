# Finance Tracker Environment Rules

## Canonical environment

- Native Windows with PowerShell 7 is the canonical environment.
- Invoke PowerShell scripts with `pwsh -NoProfile -File`.
- Use only `.venv-phase1\Scripts\python.exe`.
- Never fall back to system Python, Windows Store Python, uv Python, or another virtual environment.
- Never install dependencies during normal test execution.

## Canonical commands

- Environment check: `pwsh -NoProfile -File .\scripts\doctor.ps1`
- Bootstrap: `pwsh -NoProfile -File .\scripts\bootstrap.ps1`
- Explicit rebuild: `pwsh -NoProfile -File .\scripts\bootstrap.ps1 -Recreate`
- Tests: `pwsh -NoProfile -File .\scripts\test.ps1`

## Tool and plugin policy

Use the repository's existing instructions, focused tests, and standard
verification commands for small, well-defined fixes.

Use optional skills only when they are installed, available, and materially
useful for the task. Do not invoke a skill merely because it is available.

### Superpowers

Use Superpowers for work that genuinely requires design and implementation
planning, such as:

* complex features spanning multiple components
* database schema or migration changes
* significant architectural decisions
* unclear requirements that require structured exploration
* work with multiple viable implementation approaches and meaningful tradeoffs

Do not use Superpowers for small bug fixes, localized parser changes,
documentation edits, straightforward test additions, or narrowly defined
refactoring.

### Ponytail

Use Ponytail primarily as a final review pass for:

* unnecessary dependencies
* duplicated logic
* overengineering
* excessive abstraction
* avoidable scope expansion
* simpler implementations that preserve the required behavior

Do not invoke Superpowers and Ponytail in the same task unless the user
explicitly requests both and their roles are clearly separated.

Ponytail review does not replace:

* focused regression tests
* the complete test suite
* `git diff --check`
* complete-diff inspection
* an independent code review when one is required

### Browser and Playwright testing

Use Playwright when the changed product behavior depends on a real browser
workflow, including:

* file selection and upload
* import preview rendering
* audit status and finding presentation
* transaction filtering interactions
* warning acknowledgement
* blocked confirmation behavior
* confirmation requests
* browser-visible persistence workflows

A temporary browser interaction performed by an agent is a smoke test only. It
does not count as durable automated verification.

When a browser workflow is important to the product behavior or protects
against a confirmed regression, its Playwright test must:

* be committed to the repository
* use sanitized synthetic fixtures
* run in CI
* make deterministic assertions
* avoid real personal or financial data
* avoid depending on external network services

Do not add Playwright or another browser framework solely for a backend-only
change.

Before introducing a new browser-testing dependency:

1. inspect the repository's existing test tooling
2. prefer existing native or standard-library solutions when they provide
   meaningful coverage
3. explain why browser-level automation is necessary
4. keep the dependency and configuration change minimal
5. do not add the dependency when the user has restricted dependency changes

### Financial-data privacy

Never commit or upload:

* real bank statements
* real PayPal exports
* IBANs or account numbers
* transaction references
* personal names extracted from statements
* browser screenshots, videos, traces, or logs containing real financial data

All committed parser and browser fixtures must be synthetic and sanitized.

Browser tests must use a temporary database and must not modify the user's
permanent application database.

## Retry policy

- Never run an unchanged failed command more than twice.
- Do not try multiple equivalent Python, pip, pytest, or unittest commands after an environment failure.
- Classify every failure as environment, sandbox, permission, network, dependency, test, or application logic.
- For environment, sandbox, permission, or network failures, stop modifying application code.
- Report the exact command, current directory, interpreter, exit code, and complete error.
- Retry only after making a specific, justified change.

## Sandbox rules

- Normal tests require no network.
- All test writes must stay inside the repository.
- Do not access the real `%LOCALAPPDATA%\FinanceTracker`.
- Do not write to the user home directory or global Python installation.
- Request approval once when bootstrap genuinely requires network access.
- Do not request full-access mode merely to make tests pass.

## Encoding rules

- Source, JSON, Markdown, TOML, and PowerShell files must remain UTF-8.
- Every PowerShell script that invokes Python or captures Python text output must set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` itself; do not rely on virtual-environment activation, PowerShell profiles, the active code page, or global Windows locale settings.
- Do not pipe Chinese, German, Markdown, JSON, or source code through PowerShell text-replacement one-liners.
- Do not use `Out-File`, `Set-Content`, or `>` to rewrite source files unless encoding is explicitly controlled.
- Prefer patch edits.
- For complex edits, use Python or Node.js with explicit UTF-8 reading and writing.
- Do not rewrite an entire file unless most of it is demonstrably corrupted.
- Always review `git diff` for unexpected whole-file changes.

## Scope protection

- Environment failures must not trigger business-logic changes.
- A failing parser or domain test must be reported separately.
- Do not weaken or delete tests merely to obtain a green result.

## Final verification

- Run `git status --short`
- Run `git diff --check`
- Review `git diff`
- Run `scripts/doctor.ps1`
- Run `scripts/test.ps1`

## Definition of done

A coding task is not complete merely because the requested code was written.
Before reporting completion, the agent must:

- compare the implementation against every explicit acceptance criterion
- add or update focused tests for every changed behavior
- run `pwsh -NoProfile -File .\scripts\doctor.ps1`, `pwsh -NoProfile -File .\scripts\test.ps1`, `git diff --check`, `git status --short`, and `git --no-pager diff`
- verify that all existing tests still pass
- inspect the complete diff for unrelated changes, weakened or deleted tests, duplicated logic, compatibility regressions, unsafe money calculations, and accidental database, UI, dependency, configuration, or workflow changes
- confirm that only intended files changed
- report unproven acceptance criteria, assumptions, limitations, and remaining risks
- never claim completion while tests are failing
- never weaken a test merely to obtain a passing result
- never commit or push unless explicitly instructed

## Self-review requirements

After implementation and testing, perform a review-only pass against the complete
working-tree diff as though it were written by another engineer. Review:

- functional and business-rule correctness, including edge cases and false positives or negatives
- backward compatibility, data integrity, financial-calculation safety, and deterministic output
- test quality and documentation accuracy
- scope compliance and accidental changes outside the request

The final response must clearly separate implementation completed, automated
checks passed, self-review findings, confirmed defects fixed during self-review,
assumptions, behavior not automatically verified, and items requiring
product-owner judgment.

## GitHub operations

- Prefer the GitHub plugin/connector for remote GitHub operations when available.
- Use the GitHub plugin for:
  - pull request inspection and status
  - review comments and review threads
  - issues
  - CI and workflow inspection
  - repository metadata
  - merge-status verification

- Use local Git for:
  - status and diff
  - branch management
  - commits
  - fetch, pull, and push when Git authentication works

- On Windows, `gh` executed inside the Codex sandbox may not have access to the host user's Windows Credential Manager credentials.
- A sandbox-only `gh auth status` failure does not mean the user's GitHub account, host GitHub CLI session, or ChatGPT GitHub connector is disconnected.
- Do not repeatedly run `gh auth login` in response to sandbox-only authentication failures.
- Do not request Full Access solely to perform read-only GitHub operations when the GitHub plugin can perform them.
- If the GitHub plugin cannot perform a required operation, request narrow approval for the specific CLI operation rather than Full Access for the entire task.

## Agent skills

### Issue tracker

Issues and PRDs live in GitHub Issues for `jeremyzejunchen/finance-tracker`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read `CONTEXT.md` and relevant `docs/adr/` before design or implementation work. See `docs/agents/domain.md`.
