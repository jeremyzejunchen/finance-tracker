# Finance Tracker

Offline personal finance import, normalization, and review for Deutsche Bank PDFs, PayPal CSVs, and Trade Republic CSVs.

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

The default SQLite path is `%LOCALAPPDATA%\FinanceTracker\finance_tracker.sqlite3`.

Local account ownership and PayPal account mapping must live in `%LOCALAPPDATA%\FinanceTracker\config.json`.
Use `config.example.json` as the template and keep real IBANs, emails, and names out of the repository.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Parser parity status is tracked in `docs/phase-1-parser-parity.md`.
