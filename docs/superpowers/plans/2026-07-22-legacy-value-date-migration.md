# Legacy value_date Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely upgrade historical SQLite ledgers missing `transactions.value_date` so CSV confirmation can write transactions.

**Architecture:** Reuse `Database.initialize()` and its existing verified pre-migration backup. Extend the existing missing-transaction-column check and migration map by one defaulted column, then prove the real confirmation path succeeds against a synthetic legacy database.

**Tech Stack:** Python 3.14, SQLite, unittest, existing local Playwright CLI.

## Global Constraints

- Use only `.venv-phase1\Scripts\python.exe` and the canonical PowerShell scripts.
- Never use real financial data in tests, commits, browser artifacts, or logs.
- Preserve existing transactions and create a verified backup before any schema migration.
- Run one Playwright browser regression after implementation.

---

### Task 1: Add the legacy column migration regression and minimal implementation

**Files:**
- Modify: `tests/test_finance_tracker.py`
- Modify: `finance_tracker/db.py`
- Modify: `02_问题与报错日志.md`

**Interfaces:**
- Consumes: `Database.initialize()` and `FinanceService.confirm_many(items)`.
- Produces: a database whose `transactions` table always has `value_date` before imports run.

- [ ] **Step 1: Write the failing test**

Create a project-scoped synthetic SQLite database with a legacy `transactions` table that omits `value_date`, initialize it, then confirm one synthetic `ParsedTransaction` through `FinanceService.confirm_many()`.

```python
database.initialize()
self.assertIn("value_date", transaction_columns(database_path))
result = service.confirm_many([{"token": preview.token}])
self.assertEqual(1, result["results"][0]["inserted"])
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
.\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker.FinanceTrackerTests.test_existing_database_schema_upgrade_creates_project_backup -v
```

Expected before the implementation: an assertion failure because `value_date` is absent.

- [ ] **Step 3: Implement the minimal migration**

Add the following entry to the existing `missing_columns` map in `Database.initialize()` and include `value_date` in `_needs_transaction_columns()`:

```python
"value_date": "TEXT NOT NULL DEFAULT ''",
```

- [ ] **Step 4: Run focused and complete verification**

Run:

```powershell
pwsh -NoProfile -File .\scripts\doctor.ps1
pwsh -NoProfile -File .\scripts\test.ps1
git diff --check
```

Expected: all checks succeed, including the fixed local Playwright browser regression.

- [ ] **Step 5: Record, commit, and publish**

Record the confirmed root cause and migration result in `02_问题与报错日志.md`, stage only intended files, commit, push `codex/issue-18-merchant-review`, and update draft PR #26.
