# Finance Tracker

Offline personal finance import, normalization, and review for PayPal, Kontoumsaetze, and Trade Republic CSVs.

Phase 1 scope:

- parser parity with the legacy extractor
- normalization, audit trail, dedup, and reconciliation
- no legacy consumer auto-classification by default

Out of scope for phase 1:

- classification inbox
- rule-learning UI
- data visualization redesign

## Run

```powershell
python -m finance_tracker
```

The default SQLite path is `data\finance_tracker.sqlite3` in this project.

Local account ownership and PayPal account mapping must live in `data\config.json`. On the first default launch, compatible files from `%LOCALAPPDATA%\FinanceTracker` are copied to the project, backed up in `exports\backups\migrations`, byte-verified, and then the verified legacy originals are removed.
Use `config.example.json` as the template and keep real IBANs, emails, and names out of the repository.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Parser parity status is tracked in `docs/phase-1-parser-parity.md`.
