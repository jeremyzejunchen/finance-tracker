# Legacy transaction-write-column Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely upgrade historical SQLite ledgers missing current transaction-write columns so CSV confirmation can write transactions.

**Architecture:** Reuse `Database.initialize()` and its existing verified pre-migration backup. Extend the explicit missing-transaction-column map with only current write-path columns that have safe defaults, then prove the real confirmation path succeeds against a synthetic historical schema.

**Tech Stack:** Python 3.14, SQLite, unittest, existing local Playwright CLI.

## Global Constraints

- Use only `.venv-phase1\Scripts\python.exe` and the canonical PowerShell scripts.
- Never use real financial data in tests, commits, browser artifacts, or logs.
- Preserve existing transactions and create a verified backup before any schema migration.
- Run one Playwright browser regression after implementation.

---

### Task 1: Add the legacy column migration regression and implementation

**Files:**
- Modify: `tests/test_finance_tracker.py`
- Modify: `finance_tracker/db.py`
- Modify: `02_问题与报错日志.md`

**Interfaces:**
- Consumes: `Database.initialize()` and `FinanceService.confirm_many(items)`.
- Produces: a database whose `transactions` table has every current confirmation-write column before imports run.

- [x] **Step 1: Write the failing test**

Create a project-scoped synthetic SQLite database with a legacy `transactions` table that omits all current confirmation-write columns, initialize it, then write one synthetic prepared transaction.

```python
database.initialize()
self.assertTrue(required_columns.issubset(transaction_columns(database_path)))
result = database.write_import(source, [prepared])
self.assertEqual(1, result["inserted"])
```

- [x] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
.\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker.FinanceTrackerTests.test_schema_upgrade_recovers_all_current_import_columns -v
```

Expected before the implementation: SQLite rejects the synthetic write because a current write column is absent.

- [x] **Step 3: Implement the explicit compatibility migration**

Add the confirmed missing-column definitions to the existing `missing_columns` map in `Database.initialize()` and include the same names in `_needs_transaction_columns()`:

```python
"merchant_raw": "TEXT NOT NULL DEFAULT ''",
"transaction_type": "TEXT NOT NULL DEFAULT ''",
"source_format": "TEXT NOT NULL DEFAULT ''",
"source_record_index": "INTEGER NOT NULL DEFAULT 0",
"source_record_key": "TEXT NOT NULL DEFAULT ''",
"is_internal_transfer": "INTEGER NOT NULL DEFAULT 0",
"is_failed_transaction": "INTEGER NOT NULL DEFAULT 0",
```

- [x] **Step 4: Run focused and complete verification**

Run:

```powershell
pwsh -NoProfile -File .\scripts\doctor.ps1
pwsh -NoProfile -File .\scripts\test.ps1
git diff --check
```

Expected: all checks succeed, including the fixed local Playwright browser regression.

- [ ] **Step 5: Record, commit, and publish**

Record the confirmed root cause and migration result in `02_问题与报错日志.md`, stage only intended files, commit, push `codex/issue-18-merchant-review`, and update draft PR #26.
