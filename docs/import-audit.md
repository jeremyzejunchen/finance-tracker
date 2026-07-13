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

Planned future rules (not implemented): duplicate detection findings,
baseline-difference findings, malformed source-record diagnostics, and richer
parser-specific warning codes.
