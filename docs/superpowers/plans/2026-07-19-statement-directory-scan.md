# Statement Directory Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scan `银行流水/` recursively, identify safe account ownership, and feed selected files into the existing preview/confirmation workflow.

**Architecture:** `StatementDirectoryScanner` owns read-only discovery and account inference. `FinanceService` converts selected relative paths into existing `preview_many()` inputs; the HTTP API exposes scan and preview-by-selection, while the existing manual upload route remains unchanged.

**Tech Stack:** Python 3.14, pathlib, hashlib, unittest, local HTTP API, existing browser JavaScript.

## Global Constraints

- Scan only `银行流水/` beneath the project root; recursively accept `.pdf` and `.csv`.
- Never move, delete, upload or automatically import source files.
- `*.pdf` and `*-czj.csv` map to `ME`; `*-cr.csv` maps to `WIFE`; other CSV requires account selection and is not confirmable.
- Use SHA-256 only to mark already-imported files; committed tests use `.tmp/` synthetic files.
- Preserve existing directory-external manual upload behavior.
- Do not commit real statement files or personal financial data.

---

### Task 1: Read-only scanner and account inference

**Files:** create `finance_tracker/statement_directory.py`; modify `tests/test_finance_tracker.py`.

**Produces:** `StatementFile(relative_path, account, status, sha256)` and `StatementDirectoryScanner.scan() -> list[StatementFile]`.

- [ ] **Step 1: Write the failing recursive-discovery test**

```python
root = Path(self.directory.name) / "银行流水"
(root / "nested").mkdir(parents=True)
(root / "main.pdf").write_bytes(b"pdf")
(root / "nested" / "joint-czj.csv").write_bytes(b"czj")
(root / "nested" / "joint-cr.csv").write_bytes(b"cr")
(root / "unknown.csv").write_bytes(b"unknown")
rows = StatementDirectoryScanner(root, self.db.source_exists).scan()
self.assertEqual(["ME", "WIFE", "ME", "needs_account_selection"], [row.account for row in rows])
```

- [ ] **Step 2: Run the focused test**

Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k statement_directory -v`; expected FAIL because the scanner is absent.

- [ ] **Step 3: Implement the scanner**

```python
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.suffix.lower() not in {".pdf", ".csv"}:
        continue
    account = "ME" if path.suffix.lower() == ".pdf" or path.name.lower().endswith("-czj.csv") else "WIFE" if path.name.lower().endswith("-cr.csv") else "needs_account_selection"
```

Hash each file in chunks and set `status` to `already_imported`, `ready`, or `needs_account_selection`. Run the focused test; expected PASS.

### Task 2: Service selection and account override

**Files:** modify `finance_tracker/services.py`; modify `tests/test_finance_tracker.py`.

**Produces:** `FinanceService.scan_statement_directory()` and `FinanceService.preview_scanned_files(relative_paths)`; selected `ME`/`WIFE` files pass an account override into prepared transactions.

- [ ] **Step 1: Write the failing selection test**

```python
scan = self.service.scan_statement_directory(root)
result = self.service.preview_scanned_files([scan[0]["relative_path"]], root)
self.assertEqual("ME", result["transactions"][0]["account"])
```

- [ ] **Step 2: Run the focused test**

Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k scanned_files -v`; expected FAIL because the service methods are absent.

- [ ] **Step 3: Implement safe selection**

Resolve every requested path, reject paths outside the scan root, reject `already_imported` and `needs_account_selection` entries, then read selected bytes and call `preview_many()` with `source_path` and account override metadata. Extend `_prepare()` to replace the parsed account only when the scan supplied `ME` or `WIFE`.

- [ ] **Step 4: Add rejection and manual-upload regression tests**

Assert path traversal and unknown CSV return user-facing errors; run `preview_many` manual fixture regression unchanged. Run focused tests; expected PASS.

### Task 3: HTTP API and import-page integration

**Files:** modify `finance_tracker/app.py`; modify `finance_tracker/static/app.js`; modify `tests/test_finance_tracker.py` and add browser tests only if existing tooling supports persistent local API tests.

**Produces:** `GET /api/import/scan` and `POST /api/import/preview-scanned`; scan rows render as selectable files with account and status.

- [ ] **Step 1: Write a failing API test**

```python
response = request_json(server, "GET", "/api/import/scan")
self.assertEqual("ME", response["files"][0]["account"])
```

- [ ] **Step 2: Implement the two API routes**

`GET` returns only scan metadata. `POST` receives `{"relative_paths": [...]}` and returns the existing preview payload. Both routes return JSON user errors for unreadable or unselectable files.

- [ ] **Step 3: Render scan selection in the import page**

Add a “扫描银行流水目录” action, render status/account/path checkboxes, and submit selected paths to `preview-scanned`. Reuse `buildPreviewState`, `renderImportPreview`, and confirmation handling.

- [ ] **Step 4: Verify browser-visible behavior**

Use a committed synthetic fixture and the existing browser tooling if available; otherwise add deterministic API tests and report browser behavior as manually unverified. Manual upload remains visible and functional.

### Task 4: Privacy-safe final acceptance and publication

**Files:** modify `02_问题与报错日志.md` only for actual failures.

- [ ] **Step 1: Run the synthetic scanner, service and API tests.**

- [ ] **Step 2: Run `pwsh -NoProfile -File .\scripts\doctor.ps1`, `pwsh -NoProfile -File .\scripts\test.ps1`, diff whitespace check, status and full diff review.**

- [ ] **Step 3: Perform a local scan only if output is restricted to file count and status/account aggregates; never print statement names, paths, hashes or transaction data.**

- [ ] **Step 4: Automatically commit, push `codex/issue-17-statement-directory-scan`, and create or update its draft PR after all checks pass.**
