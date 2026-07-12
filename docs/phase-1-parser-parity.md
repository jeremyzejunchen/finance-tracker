# Phase 1 Parser Parity

This document tracks phase-1 parity between `legacy/parse_bank_pdfs.py` and the modular import pipeline in `finance_tracker`.

Status legend:

- `完整实现`
- `部分实现`
- `未实现`
- `有意改变行为`

| Capability | Legacy | New | Status | Notes |
| --- | --- | --- | --- | --- |
| 1. Deutsche Bank Transactions layout | Dedicated parser | `finance_tracker.importers.deutsche_bank.parse_transactions_layout` | 完整实现 | Keeps booking/value date parsing, payment-details merchant extraction, and header/footer filtering. |
| 2. Deutsche Bank Account Statement layout | Dedicated parser | `finance_tracker.importers.deutsche_bank.parse_account_statement_layout` | 完整实现 | Keeps split-date parsing and skips `Previous balance`. |
| 3. Merchant extraction | Inline cleanup | `finance_tracker.cleaning.merchant` + importer-specific extraction | 完整实现 | Layout-specific merchant extraction preserved. |
| 4. Amount and date parsing | Legacy helpers | `finance_tracker.importers.common` | 完整实现 | Shared parser handles German and English formats. |
| 5. Header/footer and Previous balance filtering | Inline rules | DB layout parsers | 完整实现 | Unknown PDF fallback now emits warning instead of silently replacing dedicated logic. |
| 6. PayPal English and German CSV | Supported | `finance_tracker.importers.paypal` | 完整实现 | Both column variants covered by fixtures. |
| 7. PayPal account ownership detection | Email and filename heuristics | Local config driven mapping | 有意改变行为 | Moved to `%LOCALAPPDATA%\FinanceTracker\config.json`; no private email hardcoding in repo. Covered by tests. |
| 8. PayPal to bank debit matching | Legacy heuristic | `finance_tracker.reconciliation.paypal` | 部分实现 | Automatic match requires a single candidate; ambiguous matches stay suggested. |
| 9. PayPal income and withdrawal matching | Legacy heuristic | `finance_tracker.reconciliation.paypal` | 部分实现 | Canonical/audit model exists, but fixture coverage is still narrower than legacy real-world history. |
| 10. PayPal partial balance payment | Legacy balance-aware handling | Separate PayPal rows retained with reconciliation support | 部分实现 | Duplicate suppression is safer, but legacy balance-specific inference is not fully reproduced yet. |
| 11. Trade Republic CASH transactions | Supported | `finance_tracker.importers.trade_republic` | 完整实现 | Non-CASH rows remain excluded from phase 1. |
| 12. TR SEPA debit real merchant extraction | Regex extraction | `extract_tr_merchant` | 完整实现 | Covered by fixture. |
| 13. Internal transfers across owned accounts | IBAN-based | Config-driven IBAN detection | 完整实现 | Supports DB/TR and multi-account cases through local config. |
| 14. Refunds and reversals | Post-processing pair match | `finance_tracker.reconciliation.refunds` | 部分实现 | Cross-file matching works after import; cross-batch history coverage still needs broader fixtures. |
| 15. Failed transactions | Heuristic flags | DB statement parser + excluded reason | 部分实现 | Explicit parser rule exists for statement text markers; more bank-specific variants may still exist in real statements. |
| 16. Duplicate files and overlapping statements dedup | Cache + post-process | SHA-256 file dedup + record-level fingerprints + explicit reconciliation | 有意改变行为 | Silent same-day same-amount bank dedup removed; overlapping statements now require explicit reconciliation rather than destructive suppression. |

Canonical model decision for PayPal matching:

- Canonical expense transaction: PayPal row.
- Excluded duplicate: matching Deutsche Bank PayPal debit row when confidence is automatic.
- Merchant source: PayPal merchant, because it preserves the real counterparty instead of generic `PayPal`.
- Audit trail: reconciliation row stores reason, confidence, and status between PayPal and bank transactions.

Sensitive data policy:

- Local owned-account and PayPal-account mapping must come from `%LOCALAPPDATA%\FinanceTracker\config.json`.
- Repository only ships `config.example.json` with fictional values.
- Real IBANs were previously present in git history; PR notes should recommend `git filter-repo` or equivalent history rewrite before any public release.
