# Deutsche Bank merchant extraction

The account-statement importer keeps two related values:

- `merchant_raw`: the best readable merchant or counterparty text extracted
  from the statement.
- `merchant_normalized`: a stable canonical value for filtering and later
  categorization.

The F2 account-statement parser uses deterministic patterns only. It supports
card-payment details before a structural `//` separator, `Einkauf bei ...`,
the narrowly defined `LIDL sagt Danke`/`ALDI sagt Danke` message, and the
observed canonical variants for ALDI, LIDL, KAUFLAND, TEGUT, GO ASIA, and
dm-drogerie markt. Generic metadata labels such as `Payment Reference`,
`E2E-Ref.`, `Reference`, and `Karten` are not treated as merchants.

When no safe merchant or counterparty can be determined, the parser uses
`Unknown Deutsche Bank transaction` and adds a transaction warning. That
warning flows into the existing preview and audit warning behavior.

This is conservative pattern extraction, not complete merchant recognition.
It does not use fuzzy matching, external merchant data, or historical data,
and it does not modify existing stored transactions.
