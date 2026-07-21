from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_tracker.config import FinanceTrackerConfig, default_config_path
from finance_tracker.db import Database
from finance_tracker.domain import ParsedTransaction
from finance_tracker.domain import ImportPreview
from finance_tracker.importers import ImportErrorForUser, parse_file, parse_paypal_csv, parse_trade_republic_csv
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
                    "source_types": ["kontoumsaetze_csv"],
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

    def test_kontoumsaetze_csv_is_recognized_parsed_and_redacts_sensitive_raw_fields(self):
        source_type, transactions, warnings = parse_file(
            "Kontoumsaetze_synthetic-czj.csv", self._fixture_bytes("kontoumsaetze-czj.csv"), self.config
        )

        self.assertEqual("kontoumsaetze_csv", source_type)
        self.assertFalse(warnings)
        self.assertEqual(2, len(transactions))
        self.assertEqual("2026-06-01", transactions[0].booking_date.isoformat())
        self.assertEqual("2026-06-02", transactions[0].value_date.isoformat())
        self.assertEqual("-12.34", str(transactions[0].amount))
        self.assertEqual("EUR", transactions[0].currency)
        self.assertEqual("Lastschrift", transactions[0].transaction_type)
        self.assertEqual("SYNTHETIC MARKET", transactions[0].merchant_normalized)
        self.assertEqual("ME", transactions[0].account)
        self.assertEqual("kontoumsaetze:0", transactions[0].source_record_key)
        self.assertEqual(
            {"booking_date", "value_date", "amount", "currency", "transaction_type"},
            set(transactions[0].raw),
        )
        self.assertFalse(
            any(
                token in key.casefold()
                for key in transactions[0].raw
                for token in ("iban", "bic", "referenz", "kunden", "mandat", "gläubiger", "creditor")
            )
        )

    def test_legacy_fx_rule_applies_to_kontoumsaetze_csv(self):
        config = FinanceTrackerConfig(currency_exchange_rules=[{
            "name": "Legacy FX rule",
            "source_types": ["deutsche_bank_pdf"],
            "contains_all": ["marker a", "marker b"],
        }])
        service = FinanceService(self.db, config)
        transaction = ParsedTransaction(
            booking_date=date(2026, 6, 1), amount=Decimal("100.00"), currency="EUR",
            merchant_raw="FX", merchant_normalized="FX", description_raw="marker a / marker b",
        )

        prepared = service._prepare(transaction, "kontoumsaetze_csv")

        self.assertEqual("currency_exchange", prepared["transaction_kind"])
        self.assertEqual("currency_exchange", prepared["excluded_reason"])

    def test_parse_file_rejects_pdf_with_csv_only_error(self):
        with self.assertRaisesRegex(ImportErrorForUser, "CSV"):
            parse_file("old.PDF", b"synthetic", self.config)

    def test_kontoumsaetze_rejects_missing_header_and_preview_forces_me_account(self):
        with self.assertRaisesRegex(ImportErrorForUser, "Kontoumsaetze"):
            parse_file("Kontoumsaetze_bad-czj.csv", b"not a statement", self.config)

        preview = self.service.preview("Kontoumsaetze_synthetic-czj.csv", self._fixture_bytes("kontoumsaetze-czj.csv"))
        self.assertTrue(all(transaction.account == "ME" for transaction in preview.transactions))

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

    def test_external_id_duplicate_detection_is_case_sensitive(self):
        preview = self._synthetic_preview("case.csv", "paypal_csv", [
            self._db_statement_transaction(external_id="abc", source_record_index=0),
            self._db_statement_transaction(external_id="ABC", source_record_index=1),
        ])
        result = self._preview_audit([preview])
        self.assertFalse(any(item["code"] == "DUPLICATE_EXTERNAL_ID" for item in result["audit"]["findings"]))

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

    def test_remove_pdf_source_data_is_atomic_and_idempotent(self):
        self._use_project_database()
        pdf = self.service._prepare(
            self._db_statement_transaction(source_record_key="pdf-removal"), "deutsche_bank_pdf"
        )
        paypal = self.service._prepare(
            self._db_statement_transaction(source_record_key="paypal-removal"), "paypal_csv"
        )
        trade_republic = self.service._prepare(
            self._db_statement_transaction(source_record_key="trade-removal"), "trade_republic_csv"
        )
        self.db.write_import_batch([
            ({"path": "", "filename": "legacy.pdf", "source_type": "deutsche_bank_pdf", "sha256": "pdf-removal"}, [pdf]),
            ({"path": "", "filename": "paypal.csv", "source_type": "paypal_csv", "sha256": "paypal-removal"}, [paypal]),
            ({"path": "", "filename": "trade.csv", "source_type": "trade_republic_csv", "sha256": "trade-removal"}, [trade_republic]),
        ])
        pdf_id = self.db.transaction_rows(filters={"source": "deutsche_bank_pdf"})[0]["id"]
        paypal_id = self.db.transaction_rows(filters={"source": "paypal_csv"})[0]["id"]
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO reconciliations(left_transaction_id,right_transaction_id,kind,confidence) VALUES(?,?,?,?)",
                (pdf_id, paypal_id, "synthetic", 1.0),
            )
            con.execute(
                "INSERT INTO audit_log(transaction_id,action,created_at) VALUES(?,?,?)",
                (pdf_id, "synthetic", "2026-01-01T00:00:00+00:00"),
            )

        result = self.db.remove_pdf_source_data()

        self.assertEqual({"source_files_removed": 1, "transactions_removed": 1}, result)
        self.assertEqual(0, self._source_count("deutsche_bank_pdf"))
        self.assertEqual(1, self._source_count("paypal_csv"))
        self.assertEqual(1, self._source_count("trade_republic_csv"))
        self.assertEqual(2, self.db.table_count("import_batches"))
        self.assertEqual(0, self.db.table_count("reconciliations"))
        self.assertEqual(1, self.db.audit_count("remove_pdf_source_data"))
        self.assertEqual(
            {"source_files_removed": 0, "transactions_removed": 0}, self.db.remove_pdf_source_data()
        )
        self.assertEqual(1, self.db.audit_count("remove_pdf_source_data"))
        self.assertEqual(1, len(list((Path(self.directory.name) / "project" / "exports" / "backups" / "schema").glob("*-pdf-source-removal.sqlite3"))))

    def test_remove_pdf_source_data_rolls_back_when_a_foreign_key_dependent_delete_fails(self):
        self._use_project_database()
        pdf = self.service._prepare(
            self._db_statement_transaction(source_record_key="pdf-rollback"), "deutsche_bank_pdf"
        )
        self.db.write_import(
            {"path": "", "filename": "legacy.pdf", "source_type": "deutsche_bank_pdf", "sha256": "pdf-rollback"}, [pdf]
        )
        with self.db.connect() as con:
            con.execute(
                "CREATE TRIGGER fail_pdf_transaction_removal BEFORE DELETE ON transactions "
                "BEGIN SELECT RAISE(ABORT, 'synthetic removal failure'); END"
            )

        with self.assertRaises(sqlite3.DatabaseError):
            self.db.remove_pdf_source_data()

        self.assertEqual(1, self._source_count("deutsche_bank_pdf"))
        self.assertEqual(1, self.db.table_count("transactions"))
        self.assertEqual(0, self.db.audit_count("remove_pdf_source_data"))

    def test_initialize_runs_pdf_source_removal_migration_once(self):
        self._use_project_database()
        pdf = self.service._prepare(
            self._db_statement_transaction(source_record_key="startup-removal"), "deutsche_bank_pdf"
        )
        self.db.write_import(
            {"path": "", "filename": "legacy.pdf", "source_type": "deutsche_bank_pdf", "sha256": "startup-removal"}, [pdf]
        )
        with self.db.connect() as con:
            con.execute("DELETE FROM schema_migrations WHERE version=5")

        Database(self.db.path).initialize()

        self.assertEqual(0, self._source_count("deutsche_bank_pdf"))
        self.assertEqual(1, self.db.audit_count("remove_pdf_source_data"))

    def _source_count(self, source_type: str) -> int:
        with self.db.connect() as con:
            return con.execute("SELECT COUNT(*) FROM source_files WHERE source_type=?", (source_type,)).fetchone()[0]

    def _use_project_database(self) -> None:
        self.db = Database(Path(self.directory.name) / "project" / "data" / "finance.sqlite3")
        self.db.initialize()
        self.service = FinanceService(self.db, self.config)

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

    def test_statement_directory_scan_recurses_infers_accounts_and_marks_duplicates(self):
        from finance_tracker.statement_directory import StatementDirectoryScanner

        root = Path(self.directory.name) / "银行流水"
        nested = root / "nested"
        nested.mkdir(parents=True)
        (root / "main.PDF").write_bytes(b"synthetic-pdf")
        (nested / "joint-czj.csv").write_bytes(b"synthetic-czj")
        duplicate = nested / "joint-cr.csv"
        duplicate.write_bytes(b"synthetic-cr")
        (root / "unknown.csv").write_bytes(b"synthetic-unknown")
        duplicate_hash = hashlib.sha256(duplicate.read_bytes()).hexdigest()
        self.db.write_import({"path": "", "filename": "already.csv", "source_type": "paypal_csv", "sha256": duplicate_hash}, [])

        rows = StatementDirectoryScanner(root, self.db.source_exists).scan()

        by_path = {row.relative_path: row for row in rows}
        self.assertNotIn("main.PDF", by_path)
        self.assertEqual("ME", by_path["nested/joint-czj.csv"].account)
        self.assertEqual("WIFE", by_path["nested/joint-cr.csv"].account)
        self.assertEqual("already_imported", by_path["nested/joint-cr.csv"].status)
        self.assertEqual("needs_account_selection", by_path["unknown.csv"].status)

    def test_statement_directory_scan_ignores_pdf_and_preserves_csv_account_suffixes(self):
        from finance_tracker.statement_directory import StatementDirectoryScanner

        root = Path(self.directory.name) / "银行流水"
        root.mkdir()
        (root / "old.PDF").write_bytes(b"synthetic-pdf")
        (root / "bank-czj.csv").write_bytes(b"synthetic-czj")
        (root / "bank-cr.csv").write_bytes(b"synthetic-cr")

        rows = {row.relative_path: row for row in StatementDirectoryScanner(root, self.db.source_exists).scan()}

        self.assertNotIn("old.PDF", rows)
        self.assertEqual("ME", rows["bank-czj.csv"].account)
        self.assertEqual("WIFE", rows["bank-cr.csv"].account)

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

if __name__ == "__main__":
    unittest.main()
