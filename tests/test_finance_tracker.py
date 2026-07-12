from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_tracker.config import FinanceTrackerConfig
from finance_tracker.db import Database
from finance_tracker.domain import ParsedTransaction
from finance_tracker.importers import parse_deutsche_bank_text, parse_paypal_csv, parse_trade_republic_csv
from finance_tracker.services import FinanceService


FIXTURES = Path(__file__).parent / "fixtures"


class FinanceTrackerTests(unittest.TestCase):
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
        )
        self.service = FinanceService(self.db, self.config)

    def tearDown(self):
        self.directory.cleanup()

    def _fixture_text(self, name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    def _fixture_bytes(self, name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    def _import_db_text(self, filename: str, fixture_name: str) -> None:
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text(fixture_name))
        prepared = [self.service._prepare(item, "deutsche_bank_pdf") for item in transactions]
        from finance_tracker.reconciliation import mark_internal_transfers, mark_refund_pairs

        mark_internal_transfers(prepared, self.config)
        mark_refund_pairs(prepared)
        self.db.write_import({"path": "", "filename": filename, "source_type": "deutsche_bank_pdf", "sha256": filename}, prepared)

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
        self.assertTrue(transactions[1].is_internal_transfer)
        self.assertTrue(transactions[2].is_failed_transaction)

    def test_same_day_same_amount_different_merchants_are_both_saved(self):
        transactions, _warnings = parse_deutsche_bank_text(self._fixture_text("db_transactions_layout.txt"))
        prepared = [self.service._prepare(item, "deutsche_bank_pdf") for item in transactions]
        result = self.db.write_import({"path": "", "filename": "statement.pdf", "source_type": "deutsche_bank_pdf", "sha256": "db-1"}, prepared)
        self.assertEqual(3, result["inserted"])
        merchants = [row["merchant"] for row in self.db.transaction_rows()]
        self.assertIn("EXAMPLE MARKET", merchants)
        self.assertIn("SECOND SHOP", merchants)

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
        self.assertEqual(1, len(result["blockers"]))

    def test_paypal_purchase_matches_bank_debit(self):
        reference_tx = parse_deutsche_bank_text(self._fixture_text("db_transactions_layout.txt"))[0][0]
        bank = ParsedTransaction(
            booking_date=reference_tx.booking_date,
            value_date=reference_tx.booking_date,
            amount=reference_tx.amount,
            currency="EUR",
            merchant_raw="PayPal Europe",
            merchant_normalized="PayPal Europe",
            description_raw="SEPA direct debit",
            account="Deutsche Bank",
            source_format="db_transactions",
            source_record_index=0,
            source_record_key="paypal-bank-1",
            raw={"kind": "bank_paypal"},
        )
        prepared = [self.service._prepare(bank, "deutsche_bank_pdf")]
        self.db.write_import({"path": "", "filename": "bank-paypal.pdf", "source_type": "deutsche_bank_pdf", "sha256": "bank-paypal"}, prepared)
        paypal_preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(paypal_preview.token)
        matched = [row for row in self.db.transaction_rows() if row["excluded_reason"] == "paypal_matched"]
        self.assertTrue(matched)

    def test_exact_refund_pair_is_marked_excluded(self):
        preview = self.service.preview("paypal-me.csv", self._fixture_bytes("paypal_en.csv"))
        self.service.confirm(preview.token)
        rows = self.db.transaction_rows()
        refunds = {row["external_id"]: row["excluded_reason"] for row in rows}
        self.assertEqual("matched_refund_pair", refunds["PP-1"])
        self.assertEqual("matched_refund_pair", refunds["PP-3"])

    def test_batch_confirm_rolls_back_on_duplicate_fingerprint_conflict(self):
        transactions = parse_paypal_csv(self._fixture_bytes("paypal_en.csv"), "paypal-me.csv", self.config)
        prepared = [self.service._prepare(item, "paypal_csv") for item in transactions[:1]]
        prepared.append(dict(prepared[0]))
        with self.assertRaises(Exception):
            self.db.write_import({"path": "", "filename": "dup.csv", "source_type": "paypal_csv", "sha256": "dup-source"}, prepared)
        self.assertEqual([], self.db.transaction_rows())

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

    def test_expected_snapshot_matches_prepared_rows(self):
        expected = json.loads(self._fixture_bytes("expected_transactions.json"))

        self._import_db_text("db-transactions.pdf", "db_transactions_layout.txt")
        self._import_db_text("db-statement.pdf", "db_account_statement_layout.txt")

        rows = list(reversed(self.db.transaction_rows()))
        snapshot = [
            {
                "booking_date": row["booking_date"],
                "amount": f"{row['amount_cents'] / 100:.2f}",
                "merchant": row["merchant"],
                "account": row["account"],
                "transaction_type": row["transaction_type"],
                "is_internal_transfer": bool(row["is_internal_transfer"]),
                "excluded_reason": row["excluded_reason"],
            }
            for row in rows
        ]
        self.assertEqual(expected["db_transactions_layout"] + expected["db_account_statement_layout"], snapshot)


if __name__ == "__main__":
    unittest.main()
