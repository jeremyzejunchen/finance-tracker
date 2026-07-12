from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from finance_tracker.db import Database
from finance_tracker.importers import parse_paypal_csv, parse_trade_republic_csv
from finance_tracker.services import FinanceService
from finance_tracker.domain import ParsedTransaction


class FinanceTrackerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "finance.sqlite3")
        self.db.initialize()
        self.service = FinanceService(self.db)

    def tearDown(self):
        self.directory.cleanup()

    def test_paypal_parser_reads_english_export(self):
        content = b"Date,Description,Currency,Gross,Name,Transaction ID\n01.06.2026,Payment,EUR,-12.50,Example Shop,TX-1\n"
        transactions = parse_paypal_csv(content)
        self.assertEqual(1, len(transactions))
        self.assertEqual("Example Shop", transactions[0].merchant)
        self.assertEqual("-12.50", str(transactions[0].amount))

    def test_pdf_parser_reads_amount_and_nearby_booking_date(self):
        import fitz
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Example Merchant\n-12,50\n01.06.2026")
        from finance_tracker.importers import parse_deutsche_bank_pdf
        transactions = parse_deutsche_bank_pdf(document.tobytes())
        self.assertEqual(1, len(transactions))
        self.assertEqual("-12.50", str(transactions[0].amount))

    def test_non_eur_record_is_stored_but_excluded_from_report(self):
        content = b"Date,Description,Currency,Gross,Name,Transaction ID\n01.06.2026,Payment,USD,-12.50,Example Shop,TX-1\n"
        preview = self.service.preview("paypal.csv", content)
        result = self.service.confirm(preview.token)
        self.assertEqual(1, result["rejected"])
        self.assertEqual(0, self.service.report()["count"])

    def test_trade_republic_is_investment_and_excluded_from_cashflow(self):
        content = "Date;Type;Amount;Currency;Instrument;Reference\n2026-06-01;Buy;-10,00;EUR;Sample ETF;TR-1\n".encode()
        preview = self.service.preview("trade.csv", content)
        result = self.service.confirm(preview.token)
        self.assertEqual(1, result["inserted"])
        self.assertEqual(0, self.service.report()["count"])

    def test_duplicate_source_does_not_write_twice(self):
        content = b"Date,Description,Currency,Gross,Name,Transaction ID\n01.06.2026,Payment,EUR,-12.50,Example Shop,TX-1\n"
        first = self.service.preview("paypal.csv", content)
        self.service.confirm(first.token)
        second = self.service.preview("paypal.csv", content)
        self.assertTrue(second.duplicate_source)
        self.assertTrue(self.service.confirm(second.token)["duplicate_source"])

    def test_exact_refund_pair_is_excluded(self):
        content = b"Date,Description,Currency,Gross,Name,Transaction ID\n01.06.2026,Payment,EUR,-12.50,Example Shop,TX-1\n03.06.2026,Payment Refund,EUR,12.50,Example Shop,TX-2\n"
        preview = self.service.preview("paypal.csv", content)
        self.service.confirm(preview.token)
        self.assertEqual(0, self.service.report()["count"])

    def test_manual_override_survives_report_reads(self):
        content = b"Date,Description,Currency,Gross,Name,Transaction ID\n01.06.2026,Payment,EUR,-12.50,Example Shop,TX-1\n"
        preview = self.service.preview("paypal.csv", content)
        self.service.confirm(preview.token)
        transaction = self.db.transaction_rows()[0]
        category = next(item for item in self.db.category_rows() if item["bucket"] == "expense")
        self.db.set_override(transaction["id"], category["id"], "测试")
        changed = self.db.transaction_rows()[0]
        self.assertEqual("manual_override", changed["category_reason"])

    def test_paypal_match_excludes_bank_duplicate(self):
        bank = ParsedTransaction(date(2026, 6, 2), -12.5, "EUR", "PayPal Europe", "SEPA direct debit", account="Deutsche Bank")
        paypal = ParsedTransaction(date(2026, 6, 1), -12.5, "EUR", "Example Shop", "Payment", account="PayPal", external_id="TX-1")
        cats = {row["id"]: row for row in self.db.category_rows()}
        rules = self.db.active_rules()
        prepared_bank = self.service._prepare(bank, "deutsche_bank_pdf", rules, cats)
        prepared_paypal = self.service._prepare(paypal, "paypal_csv", rules, cats)
        self.db.write_import({"path":"","filename":"bank.pdf","source_type":"deutsche_bank_pdf","sha256":"a"}, [prepared_bank])
        self.db.write_import({"path":"","filename":"paypal.csv","source_type":"paypal_csv","sha256":"b"}, [prepared_paypal])
        self.assertEqual(1, self.db.reconcile_paypal()["automatic"])
        bank_row = next(row for row in self.db.transaction_rows() if row["account"] == "Deutsche Bank")
        self.assertEqual("paypal_matched", bank_row["excluded_reason"])


if __name__ == "__main__":
    unittest.main()
