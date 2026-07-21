# Import audit

The CSV import preview includes a deterministic `audit` report. It counts input files, parsed transactions, excluded transactions, parser warnings, blockers, and exact gross totals by currency.

Gross totals include every parsed transaction once, including excluded transactions. Money uses `Decimal` and canonical strings with at least two decimals. `can_confirm` is derived from the audit status.

Finding codes:

- `DUPLICATE_SOURCE_FILE` (`blocker`): matching SHA-256 in the database or upload batch.
- `DUPLICATE_EXTERNAL_ID` (`blocker`): repeated non-empty transaction ID in one CSV source type.
- `UNSUPPORTED_CURRENCY` (`blocker`): only EUR can be confirmed.
- `IMPORT_ERROR` and `NO_TRANSACTIONS` (`blocker`): invalid or empty source.
- `TRANSACTION_WARNING` and `PARSER_WARNING` (`warning`): records requiring review.

The audit supports duplicate detection and review; it does not prove a complete or correctly categorized ledger. Refund reconciliation remains available after import. PayPal-to-bank reconciliation is unavailable because the PDF bank source is no longer imported.
