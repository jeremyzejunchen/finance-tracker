# Merchant Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist canonical merchants and tiered rules, seed the legacy baseline, and safely classify matching ledger transactions.

**Architecture:** `Database` owns schema, backups, persistence and audit writes. A pure `MerchantResolver` evaluates rules; `FinanceService` invokes it for previews, confirmed imports and historical backfill.

**Tech Stack:** Python 3.14, sqlite3, unittest, PowerShell 7.

## Global Constraints

- Use `.venv-phase1\Scripts\python.exe` and canonical PowerShell scripts only.
- Preserve UTF-8 and use patch edits; do not commit or push without explicit authorization.
- Tests are synthetic; private validation prints aggregate values only.
- Priority is manual override, exact alias, unique contains alias, then unresolved or conflict.
- Rules require matching income/expense direction; failed and non-spending flows remain unclassified.

---

### Task 1: Schema, backup, and persistence

**Files:** modify `finance_tracker/db.py` and `tests/test_finance_tracker.py`.

**Produces:** three rule tables, nullable `transactions.canonical_merchant_id`, and rule upsert/read operations.

- [ ] **Step 1: Write the failing schema test**

```python
def test_initialize_adds_merchant_rule_tables(self):
    self.assertTrue({"canonical_merchants", "merchant_aliases", "merchant_category_rules"} <= self.db.table_names())
    self.assertIn("canonical_merchant_id", self.db.table_columns("transactions"))
```

- [ ] **Step 2: Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k merchant_rule_tables -v`**

Expected: FAIL because the schema is absent.

- [ ] **Step 3: Add the minimal schema**

```python
con.execute("CREATE TABLE IF NOT EXISTS canonical_merchants (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, source TEXT NOT NULL)")
con.execute("CREATE TABLE IF NOT EXISTS merchant_aliases (id INTEGER PRIMARY KEY, canonical_merchant_id INTEGER NOT NULL REFERENCES canonical_merchants(id), pattern TEXT NOT NULL, match_kind TEXT NOT NULL CHECK(match_kind IN ('exact','contains')), source TEXT NOT NULL, UNIQUE(canonical_merchant_id,pattern,match_kind,source))")
con.execute("CREATE TABLE IF NOT EXISTS merchant_category_rules (id INTEGER PRIMARY KEY, canonical_merchant_id INTEGER NOT NULL REFERENCES canonical_merchants(id), direction TEXT NOT NULL CHECK(direction IN ('income','expense')), category_id INTEGER NOT NULL REFERENCES categories(id), source TEXT NOT NULL, UNIQUE(canonical_merchant_id,direction,category_id,source))")
```

Before altering an existing project database, copy it to `exports/backups/schema/<UTC timestamp>-<reason>.sqlite3` and compare SHA-256. Empty test databases create no backup.

- [ ] **Step 4: Add and run a rule-upsert test**

```python
merchant_id = self.db.upsert_canonical_merchant("SYNTHETIC MARKET", "test")
self.db.upsert_merchant_alias(merchant_id, "SYNTHETIC MARKET", "exact", "test")
self.db.upsert_merchant_category_rule(merchant_id, "expense", category_id, "test")
self.assertEqual("SYNTHETIC MARKET", self.db.rule_rows()[0]["canonical_merchant"])
```

Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k merchant_rule -v`; expected PASS.

### Task 2: Pure tiered resolver

**Files:** create `finance_tracker/merchant_rules.py`; modify `tests/test_finance_tracker.py`.

**Produces:** `MerchantResolution` and `MerchantResolver.resolve(merchant, amount_cents, category_status, transaction_kind, excluded_reason)`.

- [ ] **Step 1: Write the failing precedence test**

```python
resolver = MerchantResolver([rule("GENERAL SHOP", "contains", "SHOP", "expense", 1), rule("EXACT SHOP", "exact", "SHOP 42", "expense", 2)])
result = resolver.resolve("SHOP 42", -1000, "unclassified", "cash", "")
self.assertEqual(("EXACT SHOP", 2), (result.canonical_merchant, result.category_id))
```

- [ ] **Step 2: Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k resolver_prefers_exact -v`**

Expected: FAIL because `MerchantResolver` is absent.

- [ ] **Step 3: Implement the resolver**

```python
if category_status == "manual" or transaction_kind != "cash" or excluded_reason:
    return MerchantResolution.unmodified()
direction = "income" if amount_cents > 0 else "expense"
for match_kind in ("exact", "contains"):
    matches = unique_matches(rules, merchant, direction, match_kind)
    if len(matches) == 1:
        return MerchantResolution.from_rule(matches[0], match_kind)
    if len(matches) > 1:
        return MerchantResolution.conflict(match_kind)
return MerchantResolution.unresolved()
```

- [ ] **Step 4: Add direction, conflict, and manual tests; run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k resolver -v`**

```python
self.assertEqual("unclassified", resolver.resolve("SHOP", 1000, "unclassified", "cash", "").category_status)
self.assertEqual("rule_conflict_contains", resolver.resolve("SHOP", -1000, "unclassified", "cash", "").category_reason)
self.assertEqual("manual", resolver.resolve("SHOP", -1000, "manual", "cash", "").category_status)
```

### Task 3: Import-path integration

**Files:** modify `finance_tracker/services.py`, `finance_tracker/db.py`, and `tests/test_finance_tracker.py`.

**Produces:** preview and stored fields `canonical_merchant`, `canonical_merchant_id`, `category_id`, `category_status`, and `category_reason` from one shared resolution path.

- [ ] **Step 1: Write the failing equivalence test**

```python
preview = self.service.preview("synthetic.csv", self._fixture_bytes("paypal_en.csv"))
self.service.confirm(preview.token)
stored = dict(self.db.transaction_rows()[0])
self.assertEqual(preview.transactions[0]["category_reason"], stored["category_reason"])
self.assertEqual(preview.transactions[0]["canonical_merchant"], stored["canonical_merchant"])
```

- [ ] **Step 2: Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k preview_confirmation_rule -v`, then integrate `self._merchant_resolver().resolve(...)` into `_prepare()`**

Persist every returned field through `write_import_batch()`. Re-run the same command; expected PASS.

### Task 4: Legacy baseline seed and transactional backfill

**Files:** modify `finance_tracker/merchant_coverage.py`, `finance_tracker/services.py`, `finance_tracker/db.py`, and `tests/test_finance_tracker.py`.

**Produces:** `FinanceService.import_legacy_baseline_rules(path=None) -> dict[str, int]` with `rules_created`, `transactions_updated`, and `conflicts`.

- [ ] **Step 1: Write the failing idempotency test**

```python
first = self.service.import_legacy_baseline_rules(baseline_path)
second = self.service.import_legacy_baseline_rules(baseline_path)
self.assertGreater(first["rules_created"], 0)
self.assertEqual(0, second["rules_created"])
self.assertEqual(0, second["transactions_updated"])
```

- [ ] **Step 2: Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k legacy_baseline_rules -v`, then implement one transaction**

Within one `Database.connect()` transaction, map baseline categories, upsert source `legacy_baseline` merchants, contains aliases and directional rules; resolve only eligible unclassified rows; update resolved rows with `legacy_baseline_rule`; write `merchant_rule_backfill` audit entries.

- [ ] **Step 3: Add safety assertions and run baseline tests**

```python
self.db.set_override(transaction_id, category_id, "synthetic override")
result = self.service.import_legacy_baseline_rules(baseline_path)
self.assertEqual("manual", self.db.transaction_rows()[0]["category_status"])
self.assertEqual("merchant_rule_backfill", self.db.audit_rows()[0]["action"])
```

Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k "legacy_baseline or backfill" -v`; expected PASS for idempotency, manual preservation, audit and conflicts.

### Task 5: Aggregate-only acceptance

**Files:** modify `02_问题与报错日志.md` only for actual failures.

- [ ] **Step 1: Run `& .\.venv-phase1\Scripts\python.exe -m unittest discover -s tests -p test_finance_tracker.py -k merchant_coverage -v`.**

- [ ] **Step 2: Run the local coverage call only if it prints counts, coverage, date range, currency aggregates and exclusions; never transaction data.**

- [ ] **Step 3: Run `pwsh -NoProfile -File .\scripts\doctor.ps1`, then `pwsh -NoProfile -File .\scripts\test.ps1`, then the repository diff whitespace check, status and full diff inspection.**

Expected: all checks pass; remaining coverage below 95% is recorded as a product-quality gap for #18/#19, never hidden by fallback classification.
