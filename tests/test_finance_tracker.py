from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from finance_tracker.config import FinanceTrackerConfig
from finance_tracker.db import Database
from finance_tracker.domain import ParsedTransaction
from finance_tracker.domain import ImportPreview
from finance_tracker.importers import parse_deutsche_bank_text, parse_paypal_csv, parse_trade_republic_csv
from finance_tracker.importers.deutsche_bank import FALLBACK_WARNING
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
        self.db.write_import({"path": "", "filename": filename, "source_type": "deutsche_bank_pdf", "sha256": filename}, prepared)

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
        self.assertEqual(1, len(result["blockers"]))

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
