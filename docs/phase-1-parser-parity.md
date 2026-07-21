# Phase 1 CSV parser parity

Phase 1 accepts CSV sources only: PayPal (English and German layouts), Trade Republic, and Kontoumsaetze. The pipeline retains CSV normalization, source-file and record-level duplicate checks, refunds, merchant rules, internal-transfer handling, and non-spending cash-flow handling.

Deutsche Bank PDF parsing and PayPal-to-bank reconciliation are intentionally unavailable. Historical PDF parity details remain in archived specifications and implementation plans; they are not a product capability.

Sensitive account and PayPal mapping belongs only in ignored `data\config.json`. Fixtures must remain synthetic and contain no real financial data.
