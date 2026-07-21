from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_tracker.config import FinanceTrackerConfig
from finance_tracker.db import Database
from finance_tracker.domain import ParsedTransaction
from finance_tracker.importers import ImportErrorForUser, parse_file, parse_paypal_csv, parse_trade_republic_csv
from finance_tracker.services import FinanceService


FIXTURES = Path(__file__).parent / "fixtures"


class FinanceTrackerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "finance.sqlite3")
        self.db.initialize()
        self.config = FinanceTrackerConfig(
            own_accounts=[{"name": "Main Checking", "iban": "DE00123456781234567890"}],
            paypal_accounts=[{"account": "ME", "sender_emails": ["me@example.invalid"], "filename_contains": ["-me"]}],
            currency_exchange_rules=[{"name": "Synthetic exchange", "source_types": ["kontoumsaetze_csv"], "contains_all": ["fx a", "fx b"]}],
        )
        self.service = FinanceService(self.db, self.config)

    def tearDown(self):
        self.directory.cleanup()

    def fixture_bytes(self, name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    def test_parse_file_rejects_pdf_with_csv_only_error(self):
        with self.assertRaisesRegex(ImportErrorForUser, "CSV"):
            parse_file("old.PDF", b"synthetic", self.config)

    def test_parse_file_rejects_non_csv_with_csv_only_error(self):
        with self.assertRaisesRegex(ImportErrorForUser, "CSV"):
            parse_file("statement.txt", b"synthetic", self.config)

    def test_csv_importers_remain_available(self):
        source_type, kontoumsaetze, warnings = parse_file(
            "Kontoumsaetze_synthetic-czj.csv", self.fixture_bytes("kontoumsaetze-czj.csv"), self.config
        )
        self.assertEqual("kontoumsaetze_csv", source_type)
        self.assertEqual(2, len(kontoumsaetze))
        self.assertFalse(warnings)
        self.assertEqual("ME", kontoumsaetze[0].account)
        self.assertEqual("SYNTHETIC MARKET", kontoumsaetze[0].merchant_normalized)
        self.assertFalse(any("iban" in key.casefold() for key in kontoumsaetze[0].raw))
        self.assertEqual(7, len(parse_paypal_csv(self.fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)))
        self.assertEqual(3, len(parse_trade_republic_csv(self.fixture_bytes("trade_republic.csv"), self.config)))

    def test_preview_duplicate_source_and_external_id_checks_remain_csv_only(self):
        content = self.fixture_bytes("paypal_en.csv")
        result = self.service.preview_many([
            {"filename": "first.csv", "content": content},
            {"filename": "second.csv", "content": content},
        ])
        codes = {finding["code"] for finding in result["audit"]["findings"]}
        self.assertIn("DUPLICATE_SOURCE_FILE", codes)
        self.assertIn("DUPLICATE_EXTERNAL_ID", codes)
        self.assertFalse(result["can_confirm"])

    def test_refunds_remain_reconciled_after_csv_import(self):
        expense = self.transaction(date(2026, 6, 1), Decimal("-10.00"), "purchase", "expense")
        refund = self.transaction(date(2026, 6, 2), Decimal("10.00"), "refund", "refund")
        self.db.write_import({"path": "", "filename": "expense.csv", "source_type": "paypal_csv", "sha256": "expense"}, [self.service._prepare(expense, "paypal_csv")])
        self.db.write_import({"path": "", "filename": "refund.csv", "source_type": "paypal_csv", "sha256": "refund"}, [self.service._prepare(refund, "paypal_csv")])
        self.assertGreaterEqual(self.db.reconcile_refunds()["automatic"], 1)
        self.assertEqual("refund_pair", self.db.reconciliation_rows()[0]["kind"])

    def test_merchant_rules_apply_to_csv_preview_and_confirmation(self):
        category = next(row for row in self.db.category_rows() if row["bucket"] == "expense")
        merchant_id = self.db.upsert_canonical_merchant("Synthetic market", "test")
        self.db.upsert_merchant_alias(merchant_id, "synthetic market", "exact", "test")
        self.db.upsert_merchant_category_rule(merchant_id, "expense", category["id"], "test")
        preview = self.service.preview("Kontoumsaetze_synthetic-czj.csv", self.fixture_bytes("kontoumsaetze-czj.csv"))
        row = self.service._preview_rows(preview)[0]
        self.assertEqual("classified", row["category_status"])
        result = self.service.confirm(preview.token)
        self.assertFalse(result["duplicate_source"])

    def test_directory_scan_only_lists_csv_files(self):
        root = Path(self.directory.name) / "statements"
        root.mkdir()
        (root / "ME_Kontoumsaetze_synthetic-czj.csv").write_bytes(self.fixture_bytes("kontoumsaetze-czj.csv"))
        (root / "ignored.PDF").write_bytes(b"synthetic")
        paths = [item["relative_path"] for item in self.service.scan_statement_directory(root)]
        self.assertEqual(["ME_Kontoumsaetze_synthetic-czj.csv"], paths)

    def test_currency_exchange_and_report_use_csv_transactions(self):
        transaction = self.transaction(date(2026, 6, 1), Decimal("25.00"), "fx a / fx b", "exchange", merchant="FX A")
        prepared = self.service._prepare(transaction, "kontoumsaetze_csv")
        self.assertEqual("currency_exchange", prepared["transaction_kind"])
        self.assertEqual("currency_exchange", prepared["excluded_reason"])

    @staticmethod
    def transaction(booking_date, amount, description, key, merchant="SHOP"):
        return ParsedTransaction(
            booking_date=booking_date, value_date=booking_date, amount=amount, currency="EUR",
            merchant_raw=merchant, merchant_normalized=merchant, description_raw=description, account="ME",
            source_record_key=key, source_format="paypal_csv",
        )


if __name__ == "__main__":
    unittest.main()
