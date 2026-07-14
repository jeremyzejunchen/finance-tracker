# Import audit

The batch preview now includes a deterministic `audit` report. It counts input
files, parsed transactions, excluded transactions, transactions with parser
warnings, blocker findings, and exact gross totals grouped by currency.

Gross totals include every parsed transaction once, including excluded
transactions. `excluded_totals_by_currency` reports the excluded subset, and
`excluded_by_reason` groups excluded counts by reason. Money is accumulated
with `Decimal` and serialized as canonical money strings: zero is `"0.00"`,
and values have at least two decimal places (`"1.20"`). Additional meaningful
decimal places are preserved. The same convention is used for gross and
excluded totals. The audit contains only JSON-serializable primitives.

`warning_transaction_count` is the number of distinct full-batch transactions
with at least one warning. A transaction with multiple warnings counts once;
file-level parser warnings do not increment this count. `warning_finding_count`
counts the resulting warning findings.

Status meanings:

- `pass`: no warning or blocker findings; confirmation is allowed.
- `warning`: warnings exist but no blockers; confirmation is allowed.
- `blocked`: at least one blocker exists; confirmation is not allowed.

Initial finding codes are `UNSUPPORTED_CURRENCY` (blocker),
`TRANSACTION_WARNING` (warning), `PARSER_WARNING` (warning), `IMPORT_ERROR`
(blocker), and `NO_TRANSACTIONS` (blocker). Transaction-warning findings carry
the related full-batch transaction index; parser-warning findings are
file-level and have no transaction index.
Unsupported currencies are currently the only transaction-level rule that
blocks a batch. The audit does not prove that accounting data is complete,
correct, duplicate-free, correctly categorized, or correctly reconciled.
The top-level preview `can_confirm` is derived from the audit's `can_confirm`,
so the two values are guaranteed to agree.

Planned future rules (not implemented): baseline-difference findings,
malformed source-record diagnostics, and richer parser-specific warning codes.

## Duplicate and overlap findings

The preview audit also reports these deterministic findings:

- `DUPLICATE_SOURCE_FILE` (`blocker`): the uploaded file SHA256 already exists
  in the database, or the same hash occurs more than once in the current
  upload batch. It is emitted once per hash and includes every involved
  filename and upload index. Identical bytes under different filenames are one
  source identity.
- `DUPLICATE_EXTERNAL_ID` (`blocker`): two or more current-batch transactions
  share the same trimmed, non-empty external ID within the same source type.
  IDs from different source types are not combined. IDs are case-sensitive:
  trimming is applied, but case is preserved. One grouped finding is emitted
  per duplicated identity.
- `PAYPAL_BANK_MATCH` (`info`): a mutually unique PayPal/Deutsche Bank
  candidate pair. Info findings do not change a clean audit to `warning`.
- `PAYPAL_BANK_AMBIGUOUS` (`warning`): a connected candidate group where one
  side has multiple eligible candidates. Ambiguous transactions do not also
  receive a unique-match finding.

Preview PayPal-bank eligibility requires one `paypal_csv` and one
`deutsche_bank_pdf` transaction, equal supported currencies, exact equal
`Decimal` amounts including sign, dates no more than five calendar days apart
(five is inclusive), and `PAYPAL` in the bank merchant or description,
case-insensitively. Unsupported currencies are ineligible. A unique match
requires exactly one candidate on both sides. Candidate findings are audit-only:
they do not mutate
preview transactions, excluded reasons, categories, or database reconciliation
state. A candidate match is not proof that the records represent the same
real-world transaction.

Preview matching and post-import matching are related but intentionally not
identical. Post-import `reconcile_paypal_rows` retains its existing stored-row
predicate: equal stored `amount_cents`, a five-day date window, and `PAYPAL` in
the bank text. It does not apply preview currency or unsupported-currency
restrictions.

All finding indexes refer to the full batch transaction list. Duplicate-source
and external-ID blockers are also represented in the existing top-level
`blockers` field, and the top-level `can_confirm` is derived from the audit
value. General fuzzy duplicate detection and historical individual-transaction
comparison are not implemented; this audit is not complete duplicate detection
or complete PayPal reconciliation.
