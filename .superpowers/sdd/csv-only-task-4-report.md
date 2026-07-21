# CSV-only Task 4 report

## Completed

- Removed the Deutsche Bank PDF importer and all executable parser imports and calls.
- `parse_file()` rejects PDF and every non-CSV suffix with a user-facing CSV-only error.
- Restricted the import picker and statement-directory workflow to CSV files.
- Removed PayPal-to-bank preview and stored-row reconciliation, while retaining CSV duplicate checks, refunds, merchant rules, and cash-flow handling.
- Removed the PyMuPDF package dependency and environment probes.
- Removed PDF-only fixtures and replaced parser tests with synthetic CSV-only coverage.
- Updated product documentation; historical specifications and plans were left untouched.

## Verification

- Red test: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker -k pdf -v` initially failed because the old PDF error did not mention CSV.
- Scoped check passed: `rg -n "deutsche_bank_pdf|parse_deutsche_bank|PyMuPDF|\.pdf" finance_tracker tests scripts README.md docs/development-environment.md docs/import-audit.md docs/phase-1-parser-parity.md`.
- `pwsh -NoProfile -File .\scripts\doctor.ps1` passed without a PyMuPDF probe.
- `pwsh -NoProfile -File .\scripts\test.ps1` passed: 8 tests.
- `node tests\test_ui.mjs` passed.

## Remaining concern

Existing databases must already have had legacy PDF records removed by the preceding migration task. This task deliberately removes the migration code itself so the application has no executable PDF-source path.
