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
