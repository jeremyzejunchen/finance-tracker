from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_tracker.config import FinanceTrackerConfig, default_config_path
from finance_tracker.db import Database
from finance_tracker.domain import ParsedTransaction
from finance_tracker.domain import ImportPreview
from finance_tracker.importers import parse_deutsche_bank_text, parse_paypal_csv, parse_trade_republic_csv
from finance_tracker.importers.deutsche_bank import FALLBACK_WARNING, MERCHANT_WARNING, UNKNOWN_MERCHANT
from finance_tracker.reconciliation.paypal import reconcile_paypal_rows
from finance_tracker.runtime import migrate_legacy_runtime_data, project_runtime_paths
from finance_tracker.services import FinanceService


FIXTURES = Path(__file__).parent / "fixtures"


class FinanceTrackerTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "finance.sqlite3")
        self.db.initialize()
        self.config = FinanceTrackerConfig(
            own_accounts=[
                {"name": "Main Checking", "iban": "DE00123456781234567890"},
                {"name": "Broker Cash", "iban": "DE00987654329876543210"},
            ],
            paypal_accounts=[
                {"account": "ME", "sender_emails": ["me@example.invalid"], "filename_contains": ["-me"]},
                {"account": "WIFE", "sender_emails": ["wife@example.invalid"], "filename_contains": ["-wife"]},
            ],
            currency_exchange_rules=[
                {
                    "name": "Example FX remittance",
                    "source_types": ["deutsche_bank_pdf"],
                    "contains_all": ["fx marker a", "fx marker b"],
                }
            ],
        )
        self.service = FinanceService(self.db, self.config)

    def tearDown(self):
        self.directory.cleanup()

    def _fixture_text(self, name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    def _fixture_bytes(self, name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    def _preview_audit(self, previews: list[ImportPreview]) -> dict:
        by_filename = {preview.filename: preview for preview in previews}
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": by_filename[filename]
        try:
            return self.service.preview_many([{"filename": preview.filename, "content": b"synthetic"} for preview in previews])
        finally:
            self.service.preview = original_preview

    def _synthetic_preview(self, filename: str, source_type: str, transactions: list[ParsedTransaction], **overrides) -> ImportPreview:
        return ImportPreview(
            overrides.pop("token", filename), filename, source_type, overrides.pop("file_hash", filename + "-hash"),
            transactions, overrides.pop("warnings", []), overrides.pop("duplicate_source", False),
            parser_warnings=overrides.pop("parser_warnings", []),
        )

    def _import_db_text(self, filename: str, fixture_name: str) -> None:
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text(fixture_name))
        prepared = [self.service._prepare(item, "deutsche_bank_pdf") for item in transactions]
        self.db.write_import({"path": "", "filename": filename, "source_type": "deutsche_bank_pdf", "sha256": filename}, prepared)

    def _db_statement_transaction(self, **overrides) -> ParsedTransaction:
        values = {
            "booking_date": overrides.pop("booking_date", date(2026, 6, 7)),
            "value_date": overrides.pop("value_date", date(2026, 6, 7)),
            "amount": overrides.pop("amount", Decimal("2500.00")),
            "currency": overrides.pop("currency", "EUR"),
            "merchant_raw": overrides.pop("merchant_raw", "PAYM.ORDER SAMPLE REMITTER"),
            "merchant_normalized": overrides.pop("merchant_normalized", "PAYM.ORDER SAMPLE REMITTER"),
            "description_raw": overrides.pop("description_raw", "REFERENCE FX MARKER A / FX MARKER B"),
            "account": overrides.pop("account", "Deutsche Bank"),
            "transaction_type": overrides.pop("transaction_type", "SEPA Transfer (in)"),
            "source_format": overrides.pop("source_format", "db_account_statement"),
            "source_record_index": overrides.pop("source_record_index", 0),
            "source_record_key": overrides.pop("source_record_key", "db_account_statement:fx"),
            "raw": overrides.pop("raw", {"layout": "db_account_statement", "type_line": "SEPA Überweisung von PAYM.ORDER SAMPLE REMITTER", "details_lines": ["REFERENCE FX MARKER A", "FX MARKER B"]}),
        }
        values.update(overrides)
        return ParsedTransaction(**values)

    def _snapshot_from_rows(self, rows):
        return [
            {
                "booking_date": row["booking_date"],
                "value_date": row["value_date"],
                "amount": f"{row['amount_cents'] / 100:.2f}",
                "currency": row["currency"],
                "merchant_raw": row["merchant_raw"],
                "merchant_normalized": row["merchant"],
                "account": row["account"],
                "external_id": row["external_id"],
                "transaction_type": row["transaction_type"],
                "source_format": row["source_format"],
                "is_internal_transfer": bool(row["is_internal_transfer"]),
                "is_failed_transaction": bool(row["is_failed_transaction"]),
                "excluded_reason": row["excluded_reason"],
            }
            for row in rows
        ]

    def _insert_bank_paypal(self, suffix: str, merchant: str = "PayPal Europe", amount: Decimal = Decimal("-12.50")):
        reference_tx = parse_deutsche_bank_text(self._fixture_text("db_transactions_layout.txt"))[0][0]
        bank = ParsedTransaction(
            booking_date=reference_tx.booking_date,
            value_date=reference_tx.booking_date,
            amount=amount,
            currency="EUR",
            merchant_raw=merchant,
            merchant_normalized=merchant,
            description_raw="SEPA direct debit PayPal",
            account="Deutsche Bank",
            source_format="db_transactions",
            source_record_index=0,
            source_record_key=f"paypal-bank-{suffix}",
            raw={"kind": "bank_paypal", "suffix": suffix},
        )
        self.db.write_import({"path": "", "filename": f"bank-paypal-{suffix}.pdf", "source_type": "deutsche_bank_pdf", "sha256": f"bank-paypal-{suffix}"}, [self.service._prepare(bank, "deutsche_bank_pdf")])

    def _seed_review_rows(self, rows: list[tuple[str, int, str, str, str]]) -> list[int]:
        prepared = []
        for index, (merchant, amount_cents, category_status, excluded_reason, transaction_kind) in enumerate(rows):
            transaction = ParsedTransaction(
                booking_date=date(2026, 6, 1),
                value_date=date(2026, 6, 1),
                amount=Decimal(amount_cents) / 100,
                currency="EUR",
                merchant_raw=merchant,
                merchant_normalized=merchant,
                description_raw="Synthetic merchant review transaction",
                account="ME",
                source_format="synthetic",
                source_record_index=index,
                source_record_key=f"merchant-review:{index}",
                raw={"synthetic": True, "index": index},
            )
            item = self.service._prepare(transaction, "synthetic")
            item.update(
                category_status=category_status,
                category_reason="unclassified",
                excluded_reason=excluded_reason,
                transaction_kind=transaction_kind,
            )
            prepared.append(item)
        self.db.write_import(
            {"path": "", "filename": "merchant-review.csv", "source_type": "synthetic", "sha256": "merchant-review-seed"},
            prepared,
        )
        return [row["id"] for row in self.db.transaction_rows()]

    def test_merchant_review_groups_exclude_completed_and_non_spending_rows(self):
        self._seed_review_rows([
            ("SYNTHETIC SHOP", -1000, "unclassified", "", "cash"),
            ("SYNTHETIC SHOP", -2000, "unclassified", "", "cash"),
            (UNKNOWN_MERCHANT, -500, "unclassified", "", "cash"),
            ("MANUAL SHOP", -700, "manual", "", "cash"),
            ("TRANSFER", -100, "unclassified", "", "internal_transfer"),
        ])

        groups = [dict(row) for row in self.db.merchant_review_groups()]

        self.assertEqual([UNKNOWN_MERCHANT, "SYNTHETIC SHOP"], [row["merchant"] for row in groups])
        self.assertEqual(2, groups[1]["transaction_count"])
        self.assertEqual(-3000, groups[1]["amount_cents"])
        self.assertEqual(1, groups[1]["account_count"])
        self.assertEqual(2, len(self.db.merchant_review_group("SYNTHETIC SHOP", "expense")))
        self.db.skip_merchant_review_group("SYNTHETIC SHOP", "expense")
        self.assertEqual([UNKNOWN_MERCHANT], [row["merchant"] for row in self.db.merchant_review_groups()])

    def test_review_rule_backfills_group_and_preserves_manual_override(self):
        ids = self._seed_review_rows([
            ("SYNTHETIC SHOP", -1000, "unclassified", "", "cash"),
            ("SYNTHETIC SHOP", -2000, "unclassified", "", "cash"),
        ])
        category_id = next(row["id"] for row in self.db.category_rows() if row["bucket"] == "expense")
        self.db.set_override(ids[0], category_id, "synthetic exception")

        self.assertEqual(1, self.service.merchant_review_impact("SYNTHETIC SHOP", "expense")["affected_count"])
        self.assertEqual(1, self.service.apply_merchant_review_rule("SYNTHETIC SHOP", "expense", category_id)["updated_count"])

        rows = {row["id"]: row for row in self.db.transaction_rows()}
        self.assertEqual("manual", rows[ids[0]]["category_status"])
        self.assertEqual("merchant_review_rule", rows[ids[1]]["category_reason"])
        self.assertEqual(1, self.db.audit_count("merchant_review_rule_applied"))

    def test_review_rule_rejects_wrong_bucket_and_expired_group(self):
        self._seed_review_rows([("SYNTHETIC SHOP", -1000, "unclassified", "", "cash")])
        expense_category_id = next(row["id"] for row in self.db.category_rows() if row["bucket"] == "expense")
        income_category_id = next(row["id"] for row in self.db.category_rows() if row["bucket"] == "income")

        with self.assertRaisesRegex(ValueError, "分类"):
            self.service.apply_merchant_review_rule("SYNTHETIC SHOP", "expense", income_category_id)
        with self.assertRaisesRegex(ValueError, "过期"):
            self.service.apply_merchant_review_rule("MISSING SHOP", "expense", expense_category_id)

    def test_review_rule_replaces_existing_direction_rule(self):
        self._seed_review_rows([("SYNTHETIC SHOP", -1000, "unclassified", "", "cash")])
        first_expense_category_id = next(row["id"] for row in self.db.category_rows() if row["bucket"] == "expense")
        self.db.add_category("Synthetic", "Synthetic", "Alternative", "expense")
        replacement_category_id = next(row["id"] for row in self.db.category_rows() if row["level3"] == "Alternative")
        merchant_id = self.db.upsert_canonical_merchant("SYNTHETIC SHOP", "synthetic")
        self.db.upsert_merchant_alias(merchant_id, "SYNTHETIC", "contains", "synthetic")
        self.db.upsert_merchant_category_rule(merchant_id, "expense", first_expense_category_id, "synthetic")

        self.service.apply_merchant_review_rule("SYNTHETIC SHOP", "expense", replacement_category_id)

        rules = [row for row in self.db.rule_rows() if row["canonical_merchant"] == "SYNTHETIC SHOP" and row["direction"] == "expense"]
        self.assertEqual({replacement_category_id}, {row["category_id"] for row in rules})

    def test_review_rule_only_backfills_eligible_group_rows(self):
        ids = self._seed_review_rows([
            ("SYNTHETIC SHOP", -1000, "unclassified", "", "cash"),
            ("SYNTHETIC SHOP", -2000, "manual", "", "cash"),
            ("SYNTHETIC SHOP", -3000, "unclassified", "internal_transfer", "cash"),
            ("SYNTHETIC SHOP", -4000, "unclassified", "", "internal_transfer"),
        ])
        category_id = next(row["id"] for row in self.db.category_rows() if row["bucket"] == "expense")

        self.assertEqual(1, self.service.merchant_review_impact("SYNTHETIC SHOP", "expense")["affected_count"])
        self.service.apply_merchant_review_rule("SYNTHETIC SHOP", "expense", category_id)

        rows = {row["amount_cents"]: row for row in self.db.transaction_rows()}
        self.assertEqual("merchant_review_rule", rows[-1000]["category_reason"])
        self.assertEqual("manual", rows[-2000]["category_status"])
        self.assertEqual("unclassified", rows[-3000]["category_status"])
        self.assertEqual("unclassified", rows[-4000]["category_status"])

    def test_db_transactions_layout_extracts_payment_details_merchant(self):
        transactions, warnings = parse_deutsche_bank_text(self._fixture_text("db_transactions_layout.txt"))
        self.assertFalse(warnings)
        self.assertEqual(3, len(transactions))
        self.assertEqual("EXAMPLE MARKET", transactions[0].merchant_normalized)
        self.assertEqual("SECOND SHOP", transactions[2].merchant_normalized)
        self.assertEqual("Debit Card Payment", transactions[0].transaction_type)

    def test_db_account_statement_skips_previous_balance_and_reads_split_dates(self):
        transactions, warnings = parse_deutsche_bank_text(self._fixture_text("db_account_statement_layout.txt"))
        self.assertFalse(warnings)
        self.assertEqual(3, len(transactions))
        self.assertEqual("2026-06-06", transactions[0].booking_date.isoformat())
        self.assertEqual("-10.00", str(transactions[0].amount))
        self.assertNotIn("Previous balance", transactions[0].merchant_raw)

    def test_db_statement_marks_internal_and_failed_transactions(self):
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text("db_account_statement_layout.txt"))
        self.assertFalse(transactions[1].is_internal_transfer)
        self.assertTrue(transactions[2].is_failed_transaction)

    def test_db_account_statement_extracts_retailers_and_warns_for_placeholder(self):
        transactions, warnings = parse_deutsche_bank_text(self._fixture_text("db_account_statement_merchants.txt"))
        self.assertFalse(warnings)
        self.assertEqual([
            ("Tegut Filiale 2714", "TEGUT"),
            ("KAUFLAND GOETTINGEN IN", "KAUFLAND"),
            ("GO ASIA DEUTSCHLAND", "GO ASIA"),
            ("dm-drogerie markt", "dm-drogerie markt"),
            ("LIDL", "LIDL"),
            ("ALDI SÜD", "ALDI"),
        ], [(item.merchant_raw, item.merchant_normalized) for item in transactions[:6]])
        self.assertEqual(UNKNOWN_MERCHANT, transactions[6].merchant_raw)
        self.assertEqual(UNKNOWN_MERCHANT, transactions[6].merchant_normalized)
        self.assertEqual([MERCHANT_WARNING], transactions[6].warnings)

    def test_db_account_statement_keeps_valid_counterparties(self):
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text("db_account_statement_layout.txt"))
        self.assertEqual("COFFEE SHOP BERLIN", transactions[0].merchant_raw)
        self.assertEqual("PAYM.ORDER JOHN DOE", transactions[1].merchant_raw)
        self.assertEqual("FAILED MERCHANT", transactions[2].merchant_raw)

    def test_db_account_statement_retailer_patterns_override_processors(self):
        text = "\n".join([
            "Account statement",
            "- 1,00", "SEPA Lastschrifteinzug von PAYPAL", "Payment Reference/E2E-Ref. ABC Einkauf bei dm-drogerie markt", "01-01-", "2026", "01-01-", "2026", "Reference",
            "- 2,00", "SEPA Lastschrifteinzug von Verifone Payments GmbH", "Payment Reference/E2E-Ref. DEF Einkauf bei dm-drogerie markt", "02-01-", "2026", "02-01-", "2026", "Reference",
            "- 3,00", "SEPA Lastschrifteinzug von S. Digits Payment GmbH", "LIDL sagt Danke für Ihren Einkauf", "03-01-", "2026", "03-01-", "2026", "Reference",
            "- 4,00", "SEPA Lastschrifteinzug von Utility Provider GmbH", "Acme Company mention only", "04-01-", "2026", "04-01-", "2026", "Reference",
            "- 5,00", "SEPA Lastschrifteinzug von Subscription Provider GmbH", "Malformed // text", "05-01-", "2026", "05-01-", "2026", "Reference",
            "- 6,00", "Kartenzahlung", "KAUFLAND GOETTINGEN IN//GOETTINGEN/DE 24-10-2025T00:00:00 Karten", "06-01-", "2026", "06-01-", "2026", "Reference",
        ])
        transactions, _warnings = parse_deutsche_bank_text(text)
        self.assertEqual("dm-drogerie markt", transactions[0].merchant_raw)
        self.assertEqual("dm-drogerie markt", transactions[0].merchant_normalized)
        self.assertEqual("dm-drogerie markt", transactions[1].merchant_raw)
        self.assertEqual("LIDL", transactions[2].merchant_raw)
        self.assertEqual("Utility Provider GmbH", transactions[3].merchant_raw)
        self.assertEqual("Subscription Provider GmbH", transactions[4].merchant_raw)
        self.assertEqual("KAUFLAND GOETTINGEN IN", transactions[5].merchant_raw)
        self.assertEqual("KAUFLAND", transactions[5].merchant_normalized)

    def test_db_account_statement_rejects_generic_multiline_card_candidates(self):
        text = "\n".join([
            "Account statement",
            "- 1,00", "SEPA Lastschrifteinzug von Example Utility GmbH", "Payment Reference/E2E-Ref.//GOETTINGEN/DE", "01-01-2026T00:00:00 Karten", "01-01-", "2026", "01-01-", "2026", "Reference",
            "- 2,00", "Kartenzahlung", "  payment reference/e2e-ref.  //GOETTINGEN/DE", "02-01-2026T00:00:00 Karten", "02-01-", "2026", "02-01-", "2026", "Reference",
            "- 3,00", "Kartenzahlung", "KAUFLAND GOETTINGEN IN//GOETTINGEN/DE", "03-01-2026T00:00:00 Karten", "03-01-", "2026", "03-01-", "2026", "Reference",
            "- 4,00", "Kartenzahlung", "Tegut Filiale 2714//GOETTINGEN/DE", "04-01-2026T00:00:00 Karten", "04-01-", "2026", "04-01-", "2026", "Reference",
            "- 5,00", "Kartenzahlung", "GO ASIA DEUTSCHLAND//GOETTINGEN/DE", "05-01-2026T00:00:00 Karten", "05-01-", "2026", "05-01-", "2026", "Reference",
        ])
        transactions, _warnings = parse_deutsche_bank_text(text)
        self.assertEqual("Example Utility GmbH", transactions[0].merchant_raw)
        self.assertEqual(UNKNOWN_MERCHANT, transactions[1].merchant_raw)
        self.assertEqual([MERCHANT_WARNING], transactions[1].warnings)
        self.assertEqual(("KAUFLAND GOETTINGEN IN", "KAUFLAND"), (transactions[2].merchant_raw, transactions[2].merchant_normalized))
        self.assertEqual(("Tegut Filiale 2714", "TEGUT"), (transactions[3].merchant_raw, transactions[3].merchant_normalized))
        self.assertEqual(("GO ASIA DEUTSCHLAND", "GO ASIA"), (transactions[4].merchant_raw, transactions[4].merchant_normalized))

    def test_db_account_statement_rejects_generic_einkauf_candidate(self):
        text = "\n".join([
            "Account statement",
            "- 1,00", "SEPA Lastschrifteinzug von Example Utility GmbH", "Einkauf bei Payment Reference", "01-01-", "2026", "01-01-", "2026", "Reference",
        ])
        transactions, _warnings = parse_deutsche_bank_text(text)
        self.assertEqual("Example Utility GmbH", transactions[0].merchant_raw)
        self.assertNotEqual("Payment Reference", transactions[0].merchant_raw)

    def test_db_account_statement_supports_card_metadata_variants_and_skips_prefix(self):
        text = "\n".join([
            "Account statement",
            "- 1,00", "Kartenzahlung", "Payment Reference/E2E-Ref.", "Tegut Filiale 2714//Goettingen/DE", "07-08-2025T18:52:34 Karten", "07-08-", "2025", "07-08-", "2025", "Reference",
            "- 2,00", "Kartenzahlung", "ALDI NORD//GOETTINGEN/DE", "11-08-2025T20:15:02 Kartennr. 53549", "11-08-", "2025", "11-08-", "2025", "Reference",
            "- 3,00", "Kartenzahlung", "GO ASIA DEUTSCHLAND//GOETTINGEN/DE", "09-08-2025T19:25:14 Karte", "09-08-", "2025", "09-08-", "2025", "Reference",
            "- 4,00", "Kartenzahlung", "KAUFLAND GOETTINGEN IN//GOETTINGEN/DE", "10-08-2025T00:00:00 Karten", "10-08-", "2025", "10-08-", "2025", "Reference",
            "- 5,00", "Kartenzahlung", "UMG GASTRONOMIE//GOETTINGEN/DE", "12-08-2025T12:00:00 Folgenr.", "12-08-", "2025", "12-08-", "2025", "Reference",
        ])
        transactions, _warnings = parse_deutsche_bank_text(text)
        self.assertEqual([
            ("Tegut Filiale 2714", "TEGUT"),
            ("ALDI NORD", "ALDI"),
            ("GO ASIA DEUTSCHLAND", "GO ASIA"),
            ("KAUFLAND GOETTINGEN IN", "KAUFLAND"),
            ("UMG GASTRONOMIE", "UMG GASTRONOMIE"),
        ], [(item.merchant_raw, item.merchant_normalized) for item in transactions])

    def test_unresolved_db_merchant_warning_reaches_preview_audit(self):
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text("db_account_statement_merchants.txt"))
        preview = ImportPreview("merchant-warning", "statement.pdf", "deutsche_bank_pdf", "merchant-hash", transactions, [])
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": preview
        try:
            result = self.service.preview_many([{"filename": "statement.pdf", "content": b"synthetic"}])
        finally:
            self.service.preview = original_preview
        self.assertEqual(1, result["audit"]["warning_transaction_count"])
        self.assertTrue(any(finding["code"] == "TRANSACTION_WARNING" and MERCHANT_WARNING in finding["message"] for finding in result["audit"]["findings"]))

    def test_paym_order_credit_is_not_auto_internal_transfer(self):
        transaction = self._db_statement_transaction()
        prepared = self.service._prepare(transaction, "deutsche_bank_pdf")
        self.assertEqual("currency_exchange", prepared["transaction_kind"])
        self.assertEqual(0, prepared["is_internal_transfer"])

    def test_eigenkonto_still_marks_internal_transfer(self):
        transactions, _warnings = parse_deutsche_bank_text(
            "Account statement\n"
            "- 20,00\n"
            "SEPA Überweisung an EIGENKONTO\n"
            "OWN ACCOUNT\n"
            "07-06-\n"
            "2026\n"
            "07-06-\n"
            "2026\n"
            "Reference DE00987654329876543210\n"
        )
        self.assertEqual(1, len(transactions))
        self.assertTrue(transactions[0].is_internal_transfer)

    def test_currency_exchange_rule_marks_paym_order_credit(self):
        transaction = self._db_statement_transaction()
        prepared = self.service._prepare(transaction, "deutsche_bank_pdf")
        self.assertEqual("currency_exchange", prepared["transaction_kind"])
        self.assertEqual("currency_exchange", prepared["excluded_reason"])

    def test_currency_exchange_is_not_internal_transfer(self):
        transaction = self._db_statement_transaction()
        prepared = self.service._prepare(transaction, "deutsche_bank_pdf")
        self.assertEqual(0, prepared["is_internal_transfer"])

    def test_configured_own_iban_transfer_still_marks_internal_transfer(self):
        transaction = self._db_statement_transaction(
            description_raw="Transfer to own broker DE00987654329876543210",
            raw={"layout": "db_account_statement", "details_lines": ["DE00987654329876543210"]},
            source_record_key="db_account_statement:own-transfer",
        )
        prepared = self.service._prepare(transaction, "deutsche_bank_pdf")
        self.assertEqual("", prepared["excluded_reason"])
        from finance_tracker.reconciliation.transfers import mark_internal_transfers

        mark_internal_transfers([prepared], self.config)
        self.assertEqual(1, prepared["is_internal_transfer"])
        self.assertEqual("internal_transfer", prepared["excluded_reason"])

    def test_same_day_same_amount_different_merchants_are_both_saved(self):
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text("db_transactions_layout.txt"))
        prepared = [self.service._prepare(item, "deutsche_bank_pdf") for item in transactions]
        result = self.db.write_import({"path": "", "filename": "statement.pdf", "source_type": "deutsche_bank_pdf", "sha256": "db-1"}, prepared)
        self.assertEqual(3, result["inserted"])
        merchants = [row["merchant"] for row in self.db.transaction_rows()]
        self.assertIn("EXAMPLE MARKET", merchants)
        self.assertIn("SECOND SHOP", merchants)

    def test_unknown_pdf_fallback_returns_warning_and_preview_summary_includes_it(self):
        transactions, warnings = parse_deutsche_bank_text("Unknown Merchant\n01.06.2026\n-12,50\n")
        preview = ImportPreview("fallback-token", "unknown.pdf", "deutsche_bank_pdf", "hash", transactions, warnings)
        self.assertIn(FALLBACK_WARNING, preview.warnings)
        self.assertIn(FALLBACK_WARNING, preview.summary()["warnings"])

    def test_paypal_english_and_german_are_both_supported(self):
        english = parse_paypal_csv(self._fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)
        german = parse_paypal_csv(self._fixture_bytes("paypal_de.csv"), "paypal-wife.csv", self.config)
        self.assertEqual("ME", english[0].account)
        self.assertEqual("WIFE", german[0].account)
        self.assertEqual("Express Checkout Payment", german[0].transaction_type)

    def test_paypal_internal_records_are_filtered(self):
        transactions = parse_paypal_csv(self._fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)
        external_ids = {item.external_id for item in transactions}
        self.assertNotIn("PP-2", external_ids)

    def test_paypal_similar_word_real_transactions_are_kept_with_warning(self):
        english = parse_paypal_csv(self._fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)
        german = parse_paypal_csv(self._fixture_bytes("paypal_de.csv"), "paypal-wife.csv", self.config)
        suspicious = {item.external_id: item.warnings for item in english + german}
        self.assertIn("PP-7", suspicious)
        self.assertTrue(suspicious["PP-7"])
        self.assertIn("PP-DE-4", suspicious)
        self.assertTrue(suspicious["PP-DE-4"])

    def test_paypal_internal_type_filter_parameterized(self):
        cases = [
            ("Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n01.06.2026,Bank Deposit to PP Account,EUR,20.00,Top Up,TX-1,me@example.invalid\n", "paypal.csv"),
            ("Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n01.06.2026,General Authorization,EUR,-1.00,Hold,TX-2,me@example.invalid\n", "paypal.csv"),
            ("Datum,Beschreibung,Währung,Brutto,Name,Transaktionscode,Absender E-Mail-Adresse\n01.06.2026,Allgemeine Gutschrift auf Kreditkarte,EUR,\"1,00\",Top Up,TX-3,wife@example.invalid\n", "paypal-de.csv"),
            ("Datum,Beschreibung,Währung,Brutto,Name,Transaktionscode,Absender E-Mail-Adresse\n01.06.2026,Von Nutzer eingeleitete Abbuchung,EUR,\"-1,00\",Withdraw,TX-4,wife@example.invalid\n", "paypal-de.csv"),
        ]
        for content, filename in cases:
            with self.subTest(filename=filename, content=content):
                with self.assertRaises(Exception):
                    parse_paypal_csv(content.encode("utf-8"), filename, self.config)

    def test_trade_republic_extracts_real_merchant_and_internal_transfer(self):
        transactions = parse_trade_republic_csv(self._fixture_bytes("trade_republic.csv"), self.config)
        self.assertEqual(3, len(transactions))
        self.assertEqual("REAL MERCHANT", transactions[0].merchant_normalized)
        self.assertTrue(transactions[1].is_internal_transfer)
        self.assertFalse(transactions[2].is_internal_transfer)
        self.assertEqual("REAL IBAN MERCHANT", transactions[2].merchant_normalized)

    def test_batch_preview_blocks_non_eur_and_collects_baseline_diff(self):
        content = b"Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n01.06.2026,Payment,USD,-12.50,Shop,TX-1,me@example.invalid\n"
        result = self.service.preview_many([{"filename": "paypal.csv", "content": content}])
        self.assertFalse(result["can_confirm"])
        self.assertEqual(result["can_confirm"], result["audit"]["can_confirm"])
        self.assertEqual(1, len(result["blockers"]))
        self.assertEqual("blocked", result["audit"]["status"])
        self.assertFalse(result["audit"]["can_confirm"])
        self.assertEqual("UNSUPPORTED_CURRENCY", result["audit"]["findings"][0]["code"])

    def test_clean_eur_batch_has_passing_audit_and_exact_totals(self):
        content = (
            "Date,Category,Amount,Currency,Description\n"
            "01.06.2026,CASH,12.10,EUR,Salary\n"
            "02.06.2026,CASH,-2.05,EUR,Coffee\n"
        ).encode("utf-8")
        result = self.service.preview_many([
            {"filename": "clean.csv", "content": content},
        ])
        audit = result["audit"]
        self.assertEqual("pass", audit["status"])
        self.assertTrue(audit["can_confirm"])
        self.assertEqual(result["can_confirm"], audit["can_confirm"])
        self.assertEqual(2, audit["parsed_transaction_count"])
        self.assertEqual("10.05", audit["totals_by_currency"]["EUR"])

    def test_parser_warnings_are_warning_findings(self):
        preview = ImportPreview(
            "warning-preview", "unknown.pdf", "deutsche_bank_pdf", "warning-hash",
            [self._db_statement_transaction()],
            ["synthetic parser warning"],
            parser_warnings=["synthetic parser warning"],
        )
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": preview
        try:
            result = self.service.preview_many([{"filename": "unknown.pdf", "content": b"synthetic"}])
        finally:
            self.service.preview = original_preview
        audit = result["audit"]
        self.assertEqual("warning", audit["status"])
        self.assertTrue(audit["can_confirm"])
        self.assertEqual(result["can_confirm"], audit["can_confirm"])
        self.assertTrue(any(item["code"] == "PARSER_WARNING" for item in audit["findings"]))
        self.assertEqual(1, len(audit["findings"]))

    def test_transaction_warnings_are_distinct_findings_and_count_transactions_once(self):
        preview = ImportPreview(
            "transaction-warning-preview", "warning.pdf", "deutsche_bank_pdf", "warning-hash",
            [self._db_statement_transaction(warnings=["first", "second", "first"])],
            ["first", "second", "first"],
        )
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": preview
        try:
            result = self.service.preview_many([{"filename": "warning.pdf", "content": b"synthetic"}])
        finally:
            self.service.preview = original_preview
        findings = result["audit"]["findings"]
        self.assertEqual(["TRANSACTION_WARNING", "TRANSACTION_WARNING"], [item["code"] for item in findings])
        self.assertEqual(1, result["audit"]["warning_transaction_count"])
        self.assertEqual(2, result["audit"]["warning_finding_count"])

    def test_excluded_transactions_are_counted_by_reason(self):
        result = self.service.preview_many([
            {"filename": "trade.csv", "content": self._fixture_bytes("trade_republic.csv")},
        ])
        audit = result["audit"]
        self.assertEqual(1, audit["excluded_transaction_count"])
        self.assertEqual(1, audit["excluded_by_reason"]["internal_transfer"])
        self.assertTrue(audit["can_confirm"])

    def test_audit_counts_multiple_files_and_blocker_precedes_warning(self):
        unsupported = b"Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n01.06.2026,Payment,USD,-1.00,Shop,TX-1,me@example.invalid\n"
        result = self.service.preview_many([
            {"filename": "warning.csv", "content": self._fixture_bytes("paypal_en.csv")},
            {"filename": "unsupported.csv", "content": unsupported},
        ])
        self.assertEqual(2, result["audit"]["source_file_count"])
        self.assertEqual("blocked", result["audit"]["status"])
        self.assertFalse(result["audit"]["can_confirm"])

    def test_audit_indexes_are_unique_across_files_and_json_serializable(self):
        result = self.service.preview_many([
            {"filename": "first.csv", "content": self._fixture_bytes("paypal_en.csv")},
            {"filename": "second.csv", "content": self._fixture_bytes("trade_republic.csv")},
        ])
        indexes = [index for finding in result["audit"]["findings"] for index in finding["transaction_indexes"]]
        self.assertEqual(len(indexes), len(set(indexes)))
        self.assertEqual(len(result["transactions"]), result["audit"]["parsed_transaction_count"])
        for finding in result["audit"]["findings"]:
            if finding["code"] == "TRANSACTION_WARNING":
                self.assertIn(finding["message"], result["transactions"][finding["transaction_indexes"][0]]["warnings"])
        json.dumps(result["audit"])

    def test_audit_preserves_preview_fields_and_can_confirm_agreement(self):
        result = self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
        ])
        for field in ("previews", "transactions", "stats", "errors", "blockers", "can_confirm", "total_files", "baseline"):
            self.assertIn(field, result)
        self.assertEqual(result["can_confirm"], result["audit"]["can_confirm"])

    def test_mixed_currency_totals_are_separate_and_canonical(self):
        eur = b"Date,Category,Amount,Currency,Description\n01.06.2026,CASH,1.2,EUR,EUR\n"
        usd = b"Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n01.06.2026,Payment,USD,-1.20,Shop,TX-1,me@example.invalid\n"
        audit = self.service.preview_many([
            {"filename": "eur.csv", "content": eur},
            {"filename": "usd.csv", "content": usd},
        ])["audit"]
        self.assertEqual({"EUR": "1.20", "USD": "-1.20"}, audit["totals_by_currency"])

    def test_canonical_zero_money_format(self):
        content = b"Date,Category,Amount,Currency,Description\n01.06.2026,CASH,0,EUR,Zero\n"
        audit = self.service.preview_many([{"filename": "zero.csv", "content": content}])["audit"]
        self.assertEqual("0.00", audit["totals_by_currency"]["EUR"])

    def test_duplicate_source_file_is_one_blocking_finding_and_top_level_blocker(self):
        preview = self._synthetic_preview("already.csv", "paypal_csv", [], duplicate_source=True, file_hash="hash-1")
        result = self._preview_audit([preview])
        findings = [item for item in result["audit"]["findings"] if item["code"] == "DUPLICATE_SOURCE_FILE"]
        self.assertEqual(1, len(findings))
        self.assertEqual("blocker", findings[0]["severity"])
        self.assertEqual("hash-1", findings[0]["details"]["file_hash"])
        self.assertFalse(result["can_confirm"])
        self.assertFalse(result["audit"]["can_confirm"])
        self.assertTrue(any(item["error"] == "批次中存在重复源文件" for item in result["blockers"]))

    def test_repeated_duplicate_source_upload_has_one_finding(self):
        preview = self._synthetic_preview("already.csv", "paypal_csv", [], duplicate_source=True, file_hash="hash-1")
        result = self._preview_audit([preview, preview])
        self.assertEqual(1, sum(item["code"] == "DUPLICATE_SOURCE_FILE" for item in result["audit"]["findings"]))

    def test_new_source_file_has_no_duplicate_source_finding(self):
        preview = self._synthetic_preview("new.csv", "paypal_csv", [], duplicate_source=False)
        result = self._preview_audit([preview])
        self.assertFalse(any(item["code"] == "DUPLICATE_SOURCE_FILE" for item in result["audit"]["findings"]))

    def test_repeated_new_source_hash_is_one_grouped_finding_with_all_filenames(self):
        first = self._synthetic_preview("first.csv", "paypal_csv", [], file_hash="same-hash")
        second = self._synthetic_preview("renamed.csv", "trade_republic_csv", [], file_hash="same-hash")
        result = self._preview_audit([first, second])
        finding = next(item for item in result["audit"]["findings"] if item["code"] == "DUPLICATE_SOURCE_FILE")
        self.assertEqual(["first.csv", "renamed.csv"], finding["details"]["filenames"])
        self.assertEqual([0, 1], finding["details"]["upload_indexes"])
        self.assertFalse(finding["details"]["exists_in_database"])
        self.assertEqual(2, finding["details"]["occurrence_count"])

    def test_duplicate_historical_hash_is_reported_even_when_parsing_fails(self):
        content = b"not a valid pdf"
        self.db.write_import({"path": "", "filename": "old.pdf", "source_type": "deutsche_bank_pdf", "sha256": hashlib.sha256(content).hexdigest()}, [])
        result = self.service.preview_many([{"filename": "broken.pdf", "content": content}])
        finding = next(item for item in result["audit"]["findings"] if item["code"] == "DUPLICATE_SOURCE_FILE")
        self.assertEqual(["broken.pdf"], finding["details"]["filenames"])
        self.assertTrue(finding["details"]["exists_in_database"])
        self.assertFalse(result["can_confirm"])
        self.assertEqual(result["can_confirm"], result["audit"]["can_confirm"])

    def test_duplicate_external_id_is_grouped_by_source_type_and_trimmed(self):
        transactions = [
            self._db_statement_transaction(external_id=" ID-7 ", source_record_index=0),
            self._db_statement_transaction(external_id="ID-7", source_record_index=1),
            self._db_statement_transaction(external_id=" ID-7", source_record_index=2),
        ]
        result = self._preview_audit([self._synthetic_preview("duplicates.csv", "paypal_csv", transactions)])
        finding = next(item for item in result["audit"]["findings"] if item["code"] == "DUPLICATE_EXTERNAL_ID")
        self.assertEqual([0, 1, 2], finding["transaction_indexes"])
        self.assertEqual(3, finding["details"]["occurrence_count"])
        self.assertFalse(result["audit"]["can_confirm"])
        self.assertTrue(any(item["error"] == "批次内存在重复 external ID" for item in result["blockers"]))

    def test_external_id_empty_and_different_source_type_are_not_duplicates(self):
        paypal = self._synthetic_preview("paypal.csv", "paypal_csv", [
            self._db_statement_transaction(external_id="", source_record_index=0),
            self._db_statement_transaction(external_id="same", source_record_index=1),
        ])
        bank = self._synthetic_preview("bank.pdf", "deutsche_bank_pdf", [
            self._db_statement_transaction(external_id="same", source_record_index=0),
        ])
        result = self._preview_audit([paypal, bank])
        self.assertFalse(any(item["code"] == "DUPLICATE_EXTERNAL_ID" for item in result["audit"]["findings"]))

    def test_external_id_duplicate_detection_is_case_sensitive(self):
        preview = self._synthetic_preview("case.csv", "paypal_csv", [
            self._db_statement_transaction(external_id="abc", source_record_index=0),
            self._db_statement_transaction(external_id="ABC", source_record_index=1),
        ])
        result = self._preview_audit([preview])
        self.assertFalse(any(item["code"] == "DUPLICATE_EXTERNAL_ID" for item in result["audit"]["findings"]))

    def test_unique_paypal_bank_match_uses_full_batch_indexes_and_info_status(self):
        paypal = self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, 1), external_id="pp")
        bank = self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, 6), merchant_raw="PayPal Europe", merchant_normalized="PayPal Europe", external_id="bank")
        result = self._preview_audit([
            self._synthetic_preview("paypal.csv", "paypal_csv", [paypal]),
            self._synthetic_preview("bank.pdf", "deutsche_bank_pdf", [bank]),
        ])
        matches = [item for item in result["audit"]["findings"] if item["code"] == "PAYPAL_BANK_MATCH"]
        self.assertEqual(1, len(matches))
        self.assertEqual("info", matches[0]["severity"])
        self.assertEqual([0, 1], matches[0]["transaction_indexes"])
        self.assertEqual(5, matches[0]["details"]["date_difference_days"])
        self.assertEqual("-10.00", matches[0]["details"]["canonical_amount"])
        self.assertEqual(1, result["audit"]["info_finding_count"])
        self.assertEqual("pass", result["audit"]["status"])
        self.assertTrue(result["can_confirm"])

    def test_paypal_bank_match_boundaries_and_eligibility(self):
        paypal = self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, 1))
        cases = [
            (date(2026, 6, 6), "PayPal Europe", Decimal("-10.00"), "EUR", True),
            (date(2026, 6, 7), "PayPal Europe", Decimal("-10.00"), "EUR", False),
            (date(2026, 6, 6), "PayPal Europe", Decimal("10.00"), "EUR", False),
            (date(2026, 6, 6), "PayPal Europe", Decimal("-10.00"), "USD", False),
            (date(2026, 6, 6), "Other bank", Decimal("-10.00"), "EUR", False),
        ]
        for index, (booking_date, merchant, amount, currency, expected) in enumerate(cases):
            bank = self._db_statement_transaction(booking_date=booking_date, merchant_raw=merchant, merchant_normalized=merchant, amount=amount, currency=currency)
            result = self._preview_audit([
                self._synthetic_preview(f"paypal-{index}.csv", "paypal_csv", [paypal]),
                self._synthetic_preview(f"bank-{index}.pdf", "deutsche_bank_pdf", [bank]),
            ])
            has_match = any(item["code"] == "PAYPAL_BANK_MATCH" for item in result["audit"]["findings"])
            self.assertEqual(expected, has_match)

    def test_preview_paypal_matching_compares_exact_decimal_amounts(self):
        for paypal_amount, bank_amount, expected in (
            (Decimal("1.001"), Decimal("1.009"), False),
            (Decimal("1.001"), Decimal("1.001"), True),
            (Decimal("1.001"), Decimal("1.0010"), True),
            (Decimal("-1.001"), Decimal("1.001"), False),
        ):
            paypal = self._db_statement_transaction(amount=paypal_amount, booking_date=date(2026, 6, 1))
            bank = self._db_statement_transaction(amount=bank_amount, booking_date=date(2026, 6, 2), merchant_raw="PAYPAL Bank", merchant_normalized="PAYPAL Bank")
            result = self._preview_audit([
                self._synthetic_preview("paypal.csv", "paypal_csv", [paypal]),
                self._synthetic_preview("bank.pdf", "deutsche_bank_pdf", [bank]),
            ])
            self.assertEqual(expected, any(item["code"] == "PAYPAL_BANK_MATCH" for item in result["audit"]["findings"]))

    def test_post_import_paypal_matching_keeps_legacy_stored_row_predicate(self):
        paypal = {"id": 1, "amount_cents": 100, "currency": "EUR", "unsupported_currency": 1, "booking_date": "2026-06-01"}
        bank = {"id": 2, "amount_cents": 100, "currency": "USD", "unsupported_currency": 1, "booking_date": "2026-06-02", "merchant": "PAYPAL Bank", "description": ""}
        matches = reconcile_paypal_rows([paypal], [bank])
        self.assertEqual(1, len(matches))
        self.assertEqual("automatic", matches[0]["status"])

    def test_ambiguous_paypal_bank_candidates_are_one_warning_group_without_matches(self):
        paypal = self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, 1))
        banks = [self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, day), merchant_raw=f"PAYPAL Bank {day}", merchant_normalized=f"PAYPAL Bank {day}") for day in (2, 3)]
        result = self._preview_audit([
            self._synthetic_preview("paypal.csv", "paypal_csv", [paypal]),
            self._synthetic_preview("bank-a.pdf", "deutsche_bank_pdf", [banks[0]]),
            self._synthetic_preview("bank-b.pdf", "deutsche_bank_pdf", [banks[1]]),
        ])
        ambiguous = [item for item in result["audit"]["findings"] if item["code"] == "PAYPAL_BANK_AMBIGUOUS"]
        self.assertEqual(1, len(ambiguous))
        self.assertEqual("warning", ambiguous[0]["severity"])
        self.assertEqual([0, 1, 2], ambiguous[0]["transaction_indexes"])
        self.assertFalse(any(item["code"] == "PAYPAL_BANK_MATCH" for item in result["audit"]["findings"]))
        self.assertEqual("warning", result["audit"]["status"])
        self.assertTrue(result["can_confirm"])

    def test_two_paypal_transactions_sharing_one_bank_are_ambiguous(self):
        paypals = [self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, 1), external_id=f"pp-{index}") for index in (1, 2)]
        bank = self._db_statement_transaction(amount=Decimal("-10.00"), booking_date=date(2026, 6, 2), merchant_raw="PAYPAL Bank", merchant_normalized="PAYPAL Bank")
        result = self._preview_audit([
            self._synthetic_preview("paypal.csv", "paypal_csv", paypals),
            self._synthetic_preview("bank.pdf", "deutsche_bank_pdf", [bank]),
        ])
        ambiguous = [item for item in result["audit"]["findings"] if item["code"] == "PAYPAL_BANK_AMBIGUOUS"]
        self.assertEqual(1, len(ambiguous))
        self.assertEqual([0, 1, 2], ambiguous[0]["transaction_indexes"])

    def test_preview_many_returns_full_transactions_not_only_sample(self):
        header = "Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n"
        rows = [
            f"01.06.2026,Express Checkout Payment,EUR,-1.00,Shop {index},TX-{index},me@example.invalid"
            for index in range(15)
        ]
        content = (header + "\n".join(rows) + "\n").encode("utf-8")
        result = self.service.preview_many([
            {"filename": "full-paypal.csv", "content": content},
        ])
        self.assertEqual(15, result["previews"][0]["total"])
        self.assertEqual(15, len(result["transactions"]))
        self.assertEqual(15, len({item["source_record_index"] for item in result["transactions"]}))

    def test_preview_many_merges_multiple_files_and_includes_filename_and_token(self):
        result = self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
            {"filename": "trade.csv", "content": self._fixture_bytes("trade_republic.csv")},
        ])
        self.assertEqual(10, len(result["transactions"]))
        filenames = {item["filename"] for item in result["transactions"]}
        self.assertEqual({"paypal-me.csv", "trade.csv"}, filenames)
        self.assertTrue(all(item["preview_token"] for item in result["transactions"]))

    def test_preview_many_stats_are_correct(self):
        result = self.service.preview_many([
            {"filename": "trade.csv", "content": self._fixture_bytes("trade_republic.csv")},
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
        ])
        self.assertEqual(len(result["transactions"]), result["stats"]["total"])
        self.assertGreaterEqual(result["stats"]["warning_count"], 2)
        self.assertEqual(1, result["stats"]["internal_transfer_count"])
        self.assertEqual(0, result["stats"]["failed_transaction_count"])

    def test_preview_stats_include_currency_exchange_count(self):
        preview = ImportPreview(
            "preview-fx",
            "fx.pdf",
            "deutsche_bank_pdf",
            "hash-fx",
            [self._db_statement_transaction()],
            [],
        )
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": preview
        try:
            result = self.service.preview_many([{"filename": "fx.pdf", "content": b"synthetic"}])
        finally:
            self.service.preview = original_preview
        self.assertEqual(1, result["stats"]["total"])
        self.assertEqual(1, result["stats"]["currency_exchange_count"])

    def test_complete_preview_marks_currency_exchange_transaction(self):
        preview = ImportPreview(
            "preview-fx",
            "fx.pdf",
            "deutsche_bank_pdf",
            "hash-fx",
            [self._db_statement_transaction()],
            [],
        )
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": preview
        try:
            result = self.service.preview_many([{"filename": "fx.pdf", "content": b"synthetic"}])
        finally:
            self.service.preview = original_preview
        self.assertEqual("currency_exchange", result["transactions"][0]["transaction_kind"])
        self.assertFalse(result["transactions"][0]["is_internal_transfer"])

    def test_transaction_warnings_appear_in_complete_preview_response(self):
        result = self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
        ])
        warning_rows = [item for item in result["transactions"] if item["warnings"]]
        self.assertTrue(warning_rows)
        self.assertIn("PP-7", {item["external_id"] for item in warning_rows})

    def test_preview_does_not_write_to_database(self):
        self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
            {"filename": "trade-republic.csv", "content": self._fixture_bytes("trade_republic.csv")},
        ])
        self.assertEqual(0, self.db.table_count("transactions"))
        self.assertEqual(0, self.db.table_count("source_files"))
        self.assertEqual(0, self.db.table_count("import_batches"))
        self.assertEqual(0, self.db.table_count("import_runs"))

    def test_confirm_many_only_accepts_existing_preview_tokens(self):
        result = self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
        ])
        valid_token = result["previews"][0]["token"]
        with self.assertRaises(ValueError):
            self.service.confirm_many([{"token": valid_token}, {"token": "missing-token"}])

    def test_large_preview_over_two_hundred_rows_is_not_truncated(self):
        header = "Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n"
        rows = [
            f"01.06.2026,Express Checkout Payment,EUR,-1.00,Shop {index},TX-{index},me@example.invalid"
            for index in range(205)
        ]
        content = (header + "\n".join(rows) + "\n").encode("utf-8")
        result = self.service.preview_many([{"filename": "large-paypal.csv", "content": content}])
        self.assertEqual(205, result["previews"][0]["total"])
        self.assertEqual(205, len(result["transactions"]))

    def test_duplicate_source_is_marked_in_preview_transactions(self):
        preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(preview.token)
        result = self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
        ])
        self.assertTrue(result["previews"][0]["duplicate_source"])
        self.assertTrue(all(item["duplicate_source"] for item in result["transactions"]))

    def test_preview_response_keeps_original_transaction_data_when_filtered_copy_changes(self):
        result = self.service.preview_many([
            {"filename": "paypal-me.csv", "content": self._fixture_bytes("paypal_en.csv")},
        ])
        original = json.loads(json.dumps(result["transactions"]))
        filtered = [dict(item) for item in result["transactions"] if item["warnings"]]
        if filtered:
            filtered[0]["merchant_raw"] = "CHANGED"
        self.assertEqual(original, result["transactions"])

    def test_paypal_purchase_matches_bank_debit(self):
        self._insert_bank_paypal("auto", "PayPal Europe")
        paypal_preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(paypal_preview.token)
        matched = [dict(row) for row in self.db.reconciliation_rows() if row["kind"] == "paypal_bank" and row["status"] == "automatic"]
        self.assertTrue(matched)
        bank_rows = [row for row in self.db.transaction_rows() if row["merchant_raw"] == "PayPal Europe"]
        self.assertEqual("paypal_matched", bank_rows[0]["excluded_reason"])

    def test_paypal_multiple_bank_candidates_stay_suggested(self):
        self._insert_bank_paypal("cand-1", "PayPal Europe Candidate 1")
        self._insert_bank_paypal("cand-2", "PayPal Europe Candidate 2")
        paypal_preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(paypal_preview.token)
        rows = [dict(row) for row in self.db.reconciliation_rows() if row["kind"] == "paypal_bank"]
        suggested = [row for row in rows if row["status"] == "suggested"]
        self.assertTrue(suggested)
        self.assertLess(suggested[0]["confidence"], 0.9)
        bank_rows = [row for row in self.db.transaction_rows() if "PayPal Europe" in row["merchant_raw"]]
        self.assertTrue(all(row["excluded_reason"] != "paypal_matched" for row in bank_rows))

    def test_exact_refund_pair_is_marked_excluded(self):
        preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(preview.token)
        rows = self.db.transaction_rows()
        refunds = {row["external_id"]: row["excluded_reason"] for row in rows}
        self.assertEqual("matched_refund_pair", refunds["PP-1"])
        self.assertEqual("matched_refund_pair", refunds["PP-3"])

    def test_cross_batch_refund_matching_creates_automatic_reconciliation(self):
        debit = b"Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n01.06.2026,Express Checkout Payment,EUR,-18.00,Cross Batch Shop,CB-1,me@example.invalid\n"
        credit = b"Date,Description,Currency,Gross,Name,Transaction ID,From Email Address\n03.06.2026,Payment Refund,EUR,18.00,Cross Batch Shop,CB-2,me@example.invalid\n"
        first = self.service.preview("first.csv", debit)
        self.service.confirm(first.token)
        second = self.service.preview("second.csv", credit)
        self.service.confirm(second.token)
        recon = [dict(row) for row in self.db.reconciliation_rows() if row["kind"] == "refund_pair"]
        self.assertTrue(recon)
        self.assertEqual("automatic", recon[0]["status"])
        rows = {row["external_id"]: row["excluded_reason"] for row in self.db.transaction_rows()}
        self.assertEqual("matched_refund_pair", rows["CB-1"])
        self.assertEqual("matched_refund_pair", rows["CB-2"])

    def test_batch_write_import_is_atomic_across_multiple_files(self):
        transactions = parse_paypal_csv(self._fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)
        rows_a = [self.service._prepare(transactions[0], "paypal_csv")]
        rows_b = [self.service._prepare(transactions[1], "paypal_csv"), dict(self.service._prepare(transactions[1], "paypal_csv"))]
        counts_before = {table: self.db.table_count(table) for table in ("source_files", "transactions", "import_batches", "import_runs")}
        with self.assertRaises(Exception):
            self.db.write_import_batch(
                [
                    ({"path": "", "filename": "a.csv", "source_type": "paypal_csv", "sha256": "source-a"}, rows_a),
                    ({"path": "", "filename": "b.csv", "source_type": "paypal_csv", "sha256": "source-b"}, rows_b),
                ]
            )
        counts_after = {table: self.db.table_count(table) for table in ("source_files", "transactions", "import_batches", "import_runs")}
        self.assertEqual(counts_before, counts_after)

    def test_manual_override_still_works(self):
        preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(preview.token)
        transaction = self.db.transaction_rows()[0]
        category = next(item for item in self.db.category_rows() if item["bucket"] == "expense")
        self.db.set_override(transaction["id"], category["id"], "manual")
        changed = self.db.transaction_rows()[0]
        self.assertEqual("manual_override", changed["category_reason"])
        self.assertEqual("manual", changed["category_status"])

    def test_phase_1_defaults_to_unclassified_not_legacy_rules(self):
        preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(preview.token)
        row = next(row for row in self.db.transaction_rows() if row["external_id"] == "PP-4")
        self.assertEqual("unclassified", row["category_status"])
        self.assertEqual("phase_1_default", row["category_reason"])

    def test_merchant_coverage_excludes_non_spending_transactions_and_keeps_review_queue(self):
        transactions = [
            self._db_statement_transaction(merchant_raw="KAUFLAND", merchant_normalized="KAUFLAND", description_raw="", raw={}, amount=Decimal("-10.00")),
            self._db_statement_transaction(merchant_raw="UNMATCHED SHOP", merchant_normalized="UNMATCHED SHOP", description_raw="", raw={}, amount=Decimal("-5.00")),
            self._db_statement_transaction(merchant_raw="OWN ACCOUNT", merchant_normalized="OWN ACCOUNT", description_raw="", raw={}, amount=Decimal("-20.00"), is_internal_transfer=True),
            self._db_statement_transaction(merchant_raw="PAYM.ORDER EXCHANGE", merchant_normalized="PAYM.ORDER EXCHANGE", description_raw="", raw={}, amount=Decimal("100.00"), transaction_kind="currency_exchange"),
            self._db_statement_transaction(merchant_raw="FAILED SHOP", merchant_normalized="FAILED SHOP", description_raw="", raw={}, amount=Decimal("-7.00"), is_failed_transaction=True),
            self._db_statement_transaction(merchant_raw="PAYM.ORDER REMITTANCE", merchant_normalized="PAYM.ORDER REMITTANCE", description_raw="", raw={}, amount=Decimal("100.00")),
        ]
        prepared = [self.service._prepare(item, "deutsche_bank_pdf") for item in transactions]
        self.db.write_import({"path": "", "filename": "coverage.pdf", "source_type": "deutsche_bank_pdf", "sha256": "coverage-1"}, prepared)

        coverage = self.service.merchant_coverage()

        self.assertEqual(3, coverage["eligible_transactions"])
        self.assertEqual(1, coverage["baseline_matched_transactions"])
        self.assertEqual(2, coverage["pending_review_transactions"])
        self.assertEqual(33.33, coverage["coverage_percent"])
        self.assertEqual({"internal_transfer": 1, "currency_exchange": 1, "failed_transaction": 1}, coverage["excluded_by_reason"])

    def test_historical_merchant_coverage_returns_aggregates_without_transaction_content(self):
        historical_path = Path(self.directory.name) / "historical.json"
        historical_path.write_text(json.dumps({"transactions": [
            {"booking_date": "2026-01-01", "amount": "-10.00", "currency": "EUR", "merchant": "KAUFLAND"},
            {"booking_date": "2026-01-02", "amount": "-5.00", "currency": "EUR", "merchant": "UNMATCHED SHOP"},
            {"booking_date": "2026-01-03", "amount": "100.00", "currency": "EUR", "merchant": "PAYM.ORDER EXCHANGE"},
            {"booking_date": "2026-01-04", "amount": "-1.00", "merchant": "KAUFLAND"},
        ]}), encoding="utf-8")

        coverage = self.service.historical_merchant_coverage(historical_path)

        self.assertEqual(3, coverage["eligible_transactions"])
        self.assertEqual(2, coverage["baseline_matched_transactions"])
        self.assertEqual(1, coverage["pending_review_transactions"])
        self.assertEqual("2026-01-01", coverage["date_from"])
        self.assertEqual("2026-01-04", coverage["date_to"])
        self.assertEqual({"EUR": -1600}, coverage["amount_cents_by_currency"])
        self.assertEqual({"currency_exchange": 1}, coverage["excluded_by_reason"])
        self.assertNotIn("merchant", json.dumps(coverage))

    def test_project_runtime_paths_and_legacy_migration_deletes_verified_originals(self):
        project_root = Path(self.directory.name) / "project"
        legacy_root = Path(self.directory.name) / "legacy"
        legacy_root.mkdir()
        legacy_database = legacy_root / "finance_tracker.sqlite3"
        legacy_config = legacy_root / "config.json"
        legacy_database.write_bytes(b"synthetic sqlite bytes")
        legacy_config.write_text('{"own_accounts": []}', encoding="utf-8")
        expected_database = legacy_database.read_bytes()
        expected_config = legacy_config.read_text(encoding="utf-8")

        paths = project_runtime_paths(project_root)
        result = migrate_legacy_runtime_data(paths, legacy_root, timestamp="20260719-120000")

        self.assertEqual(project_root / "data" / "finance_tracker.sqlite3", paths.database_path)
        self.assertEqual(project_root / "data" / "config.json", default_config_path(project_root))
        self.assertEqual(expected_database, paths.database_path.read_bytes())
        self.assertEqual(expected_config, paths.config_path.read_text(encoding="utf-8"))
        self.assertTrue((project_root / "exports" / "backups" / "migrations" / "20260719-120000" / "finance_tracker.sqlite3").is_file())
        self.assertTrue((project_root / "exports" / "backups" / "migrations" / "20260719-120000" / "config.json").is_file())
        self.assertFalse(legacy_database.exists())
        self.assertFalse(legacy_config.exists())
        self.assertEqual(["config.json", "finance_tracker.sqlite3"], result["copied"])
        self.assertEqual([], migrate_legacy_runtime_data(paths, legacy_root, timestamp="20260719-120001")["copied"])

    def test_merchant_rule_rows_persist_canonical_merchant_alias_and_direction(self):
        category_id = self.db.category_rows()[0]["id"]

        merchant_id = self.db.upsert_canonical_merchant("SYNTHETIC MARKET", "test")
        self.db.upsert_merchant_alias(merchant_id, "SYNTHETIC MARKET", "exact", "test")
        self.db.upsert_merchant_category_rule(merchant_id, "expense", category_id, "test")

        rule = self.db.rule_rows()[0]
        self.assertEqual("SYNTHETIC MARKET", rule["canonical_merchant"])
        self.assertEqual("exact", rule["match_kind"])
        self.assertEqual("expense", rule["direction"])
        self.assertEqual(category_id, rule["category_id"])

    def test_existing_database_schema_upgrade_creates_project_backup(self):
        project_root = Path(self.directory.name) / "project"
        database_path = project_root / "data" / "finance_tracker.sqlite3"
        database_path.parent.mkdir(parents=True)
        import sqlite3
        con = sqlite3.connect(database_path)
        try:
            con.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, booking_date TEXT, source_file_id INTEGER, category_id INTEGER)")
            con.commit()
        finally:
            con.close()

        database = Database(database_path)
        database.initialize()

        backups = list((project_root / "exports" / "backups" / "schema").glob("*-canonical-merchant.sqlite3"))
        self.assertEqual(1, len(backups))
        con = sqlite3.connect(backups[0])
        try:
            self.assertNotIn("canonical_merchant_id", [row[1] for row in con.execute("PRAGMA table_info(transactions)")])
            self.assertNotIn("category_status", [row[1] for row in con.execute("PRAGMA table_info(transactions)")])
        finally:
            con.close()
        con = sqlite3.connect(database_path)
        try:
            self.assertIn("canonical_merchant_id", [row[1] for row in con.execute("PRAGMA table_info(transactions)")])
            self.assertIn("category_status", [row[1] for row in con.execute("PRAGMA table_info(transactions)")])
        finally:
            con.close()

    def test_merchant_resolver_prefers_exact_alias_over_contains_alias(self):
        from finance_tracker.merchant_rules import MerchantResolver, MerchantRule

        resolver = MerchantResolver([
            MerchantRule("GENERAL SHOP", "SHOP", "contains", "expense", 1),
            MerchantRule("EXACT SHOP", "SHOP 42", "exact", "expense", 2),
        ])

        resolution = resolver.resolve("SHOP 42", -1000, "unclassified", "cash", "")

        self.assertEqual("EXACT SHOP", resolution.canonical_merchant)
        self.assertEqual(2, resolution.category_id)
        self.assertEqual("rule_exact", resolution.category_reason)

    def test_merchant_resolver_keeps_direction_mismatch_conflict_and_manual_unclassified(self):
        from finance_tracker.merchant_rules import MerchantResolver, MerchantRule

        resolver = MerchantResolver([
            MerchantRule("EXPENSE SHOP", "SHOP", "contains", "expense", 1),
            MerchantRule("SECOND SHOP", "SHOP", "contains", "expense", 2),
        ])

        income = resolver.resolve("SHOP", 1000, "unclassified", "cash", "")
        conflict = resolver.resolve("SHOP", -1000, "unclassified", "cash", "")
        manual = resolver.resolve("SHOP", -1000, "manual", "cash", "")

        self.assertEqual("unclassified", income.category_status)
        self.assertEqual("rule_conflict_contains", conflict.category_reason)
        self.assertEqual("manual", manual.category_status)

    def test_preview_and_confirmation_share_merchant_rule_resolution(self):
        category_id = self.db.category_rows()[0]["id"]
        merchant_id = self.db.upsert_canonical_merchant("SYNTHETIC MARKET", "test")
        self.db.upsert_merchant_alias(merchant_id, "SYNTHETIC MARKET", "exact", "test")
        self.db.upsert_merchant_category_rule(merchant_id, "expense", category_id, "test")
        transaction = self._db_statement_transaction(
            merchant_raw="SYNTHETIC MARKET",
            merchant_normalized="SYNTHETIC MARKET",
            description_raw="",
            raw={},
            amount=Decimal("-10.00"),
        )
        preview = self._synthetic_preview("synthetic.pdf", "deutsche_bank_pdf", [transaction], token="rule-preview")
        original_preview = self.service.preview
        self.service.preview = lambda filename, content, source_path="": preview
        try:
            preview_rows = self.service.preview_many([{"filename": "synthetic.pdf", "content": b"synthetic"}])["transactions"]
        finally:
            self.service.preview = original_preview

        self.service.previews[preview.token] = preview
        self.service.confirm("rule-preview")
        stored = self.db.transaction_rows()[0]
        self.assertEqual("SYNTHETIC MARKET", preview_rows[0]["canonical_merchant"])
        self.assertEqual(preview_rows[0]["category_reason"], stored["category_reason"])
        self.assertEqual(category_id, stored["category_id"])

    def test_legacy_baseline_import_backfills_once_and_preserves_manual_override(self):
        baseline_path = Path(self.directory.name) / "legacy-categories.md"
        baseline_path.write_text(
            "## 活动支出\n\n| 子类 | 关键字/商户 | 说明 |\n|------|-----------|------|\n| 合成超市 | SYNTHETIC MARKET | test |\n",
            encoding="utf-8",
        )
        first = self._db_statement_transaction(merchant_raw="SYNTHETIC MARKET", merchant_normalized="SYNTHETIC MARKET", description_raw="", raw={}, amount=Decimal("-10.00"))
        second = self._db_statement_transaction(merchant_raw="SYNTHETIC MARKET", merchant_normalized="SYNTHETIC MARKET", description_raw="manual", raw={}, amount=Decimal("-20.00"), external_id="manual-row")
        prepared = [self.service._prepare(item, "deutsche_bank_pdf") for item in (first, second)]
        self.db.write_import({"path": "", "filename": "synthetic.pdf", "source_type": "deutsche_bank_pdf", "sha256": "legacy-baseline-seed"}, prepared)
        manual_category_id = self.db.category_rows()[0]["id"]
        manual_transaction_id = self.db.transaction_rows()[0]["id"]
        self.db.set_override(manual_transaction_id, manual_category_id, "synthetic override")

        first_result = self.service.import_legacy_baseline_rules(baseline_path)
        second_result = self.service.import_legacy_baseline_rules(baseline_path)

        rows = self.db.transaction_rows()
        backfilled = next(row for row in rows if row["merchant"] == "SYNTHETIC MARKET" and row["category_status"] != "manual")
        manual = next(row for row in rows if row["category_status"] == "manual")
        self.assertEqual(1, first_result["rules_created"])
        self.assertEqual(1, first_result["transactions_updated"])
        self.assertEqual(0, second_result["rules_created"])
        self.assertEqual(0, second_result["transactions_updated"])
        self.assertEqual("合成超市", backfilled["level3"])
        self.assertEqual("legacy_baseline_rule", backfilled["category_reason"])
        self.assertEqual("manual", manual["category_status"])
        self.assertEqual(1, self.db.audit_count("merchant_rule_backfill"))

    def test_statement_directory_scan_recurses_infers_accounts_and_marks_duplicates(self):
        from finance_tracker.statement_directory import StatementDirectoryScanner

        root = Path(self.directory.name) / "银行流水"
        nested = root / "nested"
        nested.mkdir(parents=True)
        (root / "main.pdf").write_bytes(b"synthetic-pdf")
        (nested / "joint-czj.csv").write_bytes(b"synthetic-czj")
        duplicate = nested / "joint-cr.csv"
        duplicate.write_bytes(b"synthetic-cr")
        (root / "unknown.csv").write_bytes(b"synthetic-unknown")
        duplicate_hash = hashlib.sha256(duplicate.read_bytes()).hexdigest()
        self.db.write_import({"path": "", "filename": "already.csv", "source_type": "paypal_csv", "sha256": duplicate_hash}, [])

        rows = StatementDirectoryScanner(root, self.db.source_exists).scan()

        by_path = {row.relative_path: row for row in rows}
        self.assertEqual("ME", by_path["main.pdf"].account)
        self.assertEqual("ME", by_path["nested/joint-czj.csv"].account)
        self.assertEqual("WIFE", by_path["nested/joint-cr.csv"].account)
        self.assertEqual("already_imported", by_path["nested/joint-cr.csv"].status)
        self.assertEqual("needs_account_selection", by_path["unknown.csv"].status)

    def test_preview_scanned_file_uses_inferred_account(self):
        root = Path(self.directory.name) / "银行流水"
        root.mkdir()
        statement = root / "paypal-czj.csv"
        statement.write_bytes(self._fixture_bytes("paypal_en.csv"))

        result = self.service.preview_scanned_files(["paypal-czj.csv"], root)

        self.assertEqual("ME", result["transactions"][0]["account"])

    def test_server_uses_injected_statement_directory(self):
        from finance_tracker.app import build_server

        root = Path(self.directory.name) / "scan-root"
        root.mkdir()
        server = build_server("127.0.0.1", 0, Path(self.directory.name) / "server.sqlite3", root)
        try:
            self.assertEqual(root.resolve(), server.RequestHandlerClass.statement_root)
        finally:
            server.server_close()

    def test_legacy_migration_keeps_originals_when_project_data_already_exists(self):
        project_root = Path(self.directory.name) / "project"
        legacy_root = Path(self.directory.name) / "legacy"
        legacy_root.mkdir()
        legacy_database = legacy_root / "finance_tracker.sqlite3"
        legacy_database.write_bytes(b"synthetic legacy sqlite bytes")
        paths = project_runtime_paths(project_root)
        paths.data_dir.mkdir(parents=True)
        paths.database_path.write_bytes(b"existing project sqlite bytes")

        result = migrate_legacy_runtime_data(paths, legacy_root)

        self.assertEqual([], result["copied"])
        self.assertEqual(["finance_tracker.sqlite3"], result["skipped"])
        self.assertTrue(legacy_database.is_file())
        self.assertEqual(b"existing project sqlite bytes", paths.database_path.read_bytes())

    def test_currency_exchange_is_written_with_excluded_reason(self):
        transaction = self._db_statement_transaction()
        self.db.write_import(
            {"path": "", "filename": "fx.pdf", "source_type": "deutsche_bank_pdf", "sha256": "fx-1"},
            [self.service._prepare(transaction, "deutsche_bank_pdf")],
        )
        row = self.db.transaction_rows()[0]
        self.assertEqual("currency_exchange", row["excluded_reason"])
        self.assertEqual("currency_exchange", row["transaction_kind"])
        self.assertEqual(0, row["is_internal_transfer"])

    def test_currency_exchange_is_excluded_from_report_income_and_net(self):
        self.db.write_import(
            {"path": "", "filename": "fx.pdf", "source_type": "deutsche_bank_pdf", "sha256": "fx-1"},
            [self.service._prepare(self._db_statement_transaction(), "deutsche_bank_pdf")],
        )
        report = self.service.report()
        self.assertEqual(0, report["income"])
        self.assertEqual(0, report["net"])
        self.assertEqual(0, report["count"])

    def test_expected_snapshot_matches_prepared_rows(self):
        expected = json.loads(self._fixture_bytes("expected_transactions.json"))
        db_transactions, _ = parse_deutsche_bank_text(self._fixture_text("db_transactions_layout.txt"))
        db_statement, _ = parse_deutsche_bank_text(self._fixture_text("db_account_statement_layout.txt"))
        paypal_en = parse_paypal_csv(self._fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)
        paypal_de = parse_paypal_csv(self._fixture_bytes("paypal_de.csv"), "paypal-wife.csv", self.config)
        trade_republic = parse_trade_republic_csv(self._fixture_bytes("trade_republic.csv"), self.config)

        self.assertEqual(expected["deutsche_bank_transactions"], self._snapshot_from_parsed(db_transactions))
        self.assertEqual(expected["deutsche_bank_statement"], self._snapshot_from_parsed(db_statement))
        self.assertEqual(expected["paypal_english_kept_ids"], [item.external_id for item in paypal_en])
        self.assertEqual(expected["paypal_german_kept_ids"], [item.external_id for item in paypal_de])
        self.assertEqual(expected["trade_republic"], self._snapshot_from_parsed(trade_republic))

    def test_reconciliation_snapshot_matches_expected(self):
        expected = json.loads(self._fixture_bytes("expected_transactions.json"))
        self._insert_bank_paypal("snap-auto", "PayPal Europe")
        paypal_preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(paypal_preview.token)
        paypal_auto = [dict(row) for row in self.db.reconciliation_rows() if row["kind"] == "paypal_bank" and row["status"] == "automatic"][0]
        self.assertEqual(expected["reconciliations"]["paypal_bank_automatic"], self._reconciliation_shape(paypal_auto))

    def test_suggested_reconciliation_snapshot_matches_expected(self):
        expected = json.loads(self._fixture_bytes("expected_transactions.json"))
        self._insert_bank_paypal("snap-cand-1", "PayPal Europe Candidate 1")
        self._insert_bank_paypal("snap-cand-2", "PayPal Europe Candidate 2")
        paypal_preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(paypal_preview.token)
        paypal_suggested = [dict(row) for row in self.db.reconciliation_rows() if row["kind"] == "paypal_bank" and row["status"] == "suggested"][0]
        self.assertEqual(expected["reconciliations"]["paypal_bank_suggested"], self._reconciliation_shape(paypal_suggested))

    def _snapshot_from_parsed(self, rows):
        return [
            {
                "booking_date": item.booking_date.isoformat(),
                "value_date": (item.value_date or item.booking_date).isoformat(),
                "amount": str(item.amount),
                "currency": item.currency,
                "merchant_raw": item.merchant_raw,
                "merchant_normalized": item.merchant_normalized,
                "account": item.account,
                "external_id": item.external_id,
                "transaction_type": item.transaction_type,
                "source_format": item.source_format,
                "is_internal_transfer": item.is_internal_transfer,
                "is_failed_transaction": item.is_failed_transaction,
                "excluded_reason": "internal_transfer" if item.is_internal_transfer else "failed_transaction" if item.is_failed_transaction else "",
            }
            for item in rows
        ]

    def _reconciliation_shape(self, row):
        return {
            "kind": row["kind"],
            "reason": row["reason"],
            "confidence": row["confidence"],
            "status": row["status"],
        }


if __name__ == "__main__":
    unittest.main()
