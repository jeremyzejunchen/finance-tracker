from __future__ import annotations

from pathlib import Path

from ..config import FinanceTrackerConfig
from ..domain import ParsedTransaction
from .common import ImportErrorForUser
from .deutsche_bank import parse_pdf as parse_deutsche_bank_pdf, parse_text as parse_deutsche_bank_text
from .kontoumsaetze import is_kontoumsaetze_csv, is_kontoumsaetze_filename, parse_kontoumsaetze_csv
from .paypal import parse_paypal_csv
from .trade_republic import parse_trade_republic_csv


def detect_source(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        raise ImportErrorForUser("PDF 导入将在后续任务中支持。")
    if suffix != ".csv":
        raise ImportErrorForUser("仅支持 PDF 或 CSV 文件。")
    if is_kontoumsaetze_filename(filename):
        if is_kontoumsaetze_csv(filename, content):
            return "kontoumsaetze_csv"
        raise ImportErrorForUser("Kontoumsaetze CSV 缺少必需交易表头。")
    header = content[:4096].decode("utf-8-sig", errors="ignore").lower()
    if any(key in header for key in ("transaktionscode", "transaction id", "paypal")):
        return "paypal_csv"
    if any(key in header for key in ("trade republic", "cash account", "wertstellung", "isin", "transaction type", "counterparty_iban")):
        return "trade_republic_csv"
    return "trade_republic_csv"


def parse_file(filename: str, content: bytes, config: FinanceTrackerConfig) -> tuple[str, list[ParsedTransaction], list[str]]:
    source_type = detect_source(filename, content)
    if source_type == "deutsche_bank_pdf":
        transactions, warnings = parse_deutsche_bank_pdf(content)
        return source_type, transactions, warnings
    if source_type == "paypal_csv":
        return source_type, parse_paypal_csv(content, filename, config), []
    if source_type == "kontoumsaetze_csv":
        return source_type, parse_kontoumsaetze_csv(content, filename), []
    return source_type, parse_trade_republic_csv(content, config), []


__all__ = [
    "ImportErrorForUser",
    "detect_source",
    "parse_deutsche_bank_pdf",
    "parse_deutsche_bank_text",
    "parse_file",
    "parse_kontoumsaetze_csv",
    "parse_paypal_csv",
    "parse_trade_republic_csv",
]
