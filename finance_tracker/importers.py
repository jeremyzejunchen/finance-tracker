from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz

from .domain import ParsedTransaction


DATE_RE = re.compile(r"(?P<date>\d{2}[./-]\d{2}[./-]\d{2,4})")
AMOUNT_RE = re.compile(r"(?P<amount>[+-]?\s?\d{1,3}(?:[. ]\d{3})*[,\.]\d{2})")


class ImportErrorForUser(ValueError):
    pass


def detect_source(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "deutsche_bank_pdf"
    if suffix != ".csv":
        raise ImportErrorForUser("仅支持 PDF 或 CSV 文件。")
    header = content[:4096].decode("utf-8-sig", errors="ignore").lower()
    if any(key in header for key in ("transaktionscode", "transaction id", "paypal")):
        return "paypal_csv"
    if any(key in header for key in ("trade republic", "cash account", "wertstellung", "isin")):
        return "trade_republic_csv"
    # Trade Republic exports vary by language; a generic CSV is treated as TR and previewed.
    return "trade_republic_csv"


def parse_file(filename: str, content: bytes) -> tuple[str, list[ParsedTransaction], list[str]]:
    source_type = detect_source(filename, content)
    if source_type == "deutsche_bank_pdf":
        return source_type, parse_deutsche_bank_pdf(content), []
    if source_type == "paypal_csv":
        return source_type, parse_paypal_csv(content), []
    return source_type, parse_trade_republic_csv(content), []


def parse_deutsche_bank_pdf(content: bytes) -> list[ParsedTransaction]:
    document = fitz.open(stream=content, filetype="pdf")
    lines = []
    for page in document:
        lines.extend(line.strip() for line in page.get_text("text").splitlines() if line.strip())
    result: list[ParsedTransaction] = []
    for index, line in enumerate(lines):
        date_spans = [match.span() for match in DATE_RE.finditer(line)]
        amounts = [match for match in AMOUNT_RE.finditer(line)
                   if not any(match.start() >= start and match.end() <= end for start, end in date_spans)]
        if not amounts:
            continue
        amount = parse_amount(amounts[-1].group("amount"))
        if amount is None:
            continue
        date_match = nearest_date(lines, index)
        if not date_match:
            continue
        date_value = parse_date(date_match.group("date"))
        if not date_value:
            continue
        remainder = line[:amounts[-1].start()].strip(" -")
        detail = " ".join(lines[index + 1:index + 8])
        merchant = clean_merchant(remainder or detail or "未识别商户")
        result.append(ParsedTransaction(date_value, amount, "EUR", merchant, detail, account="Deutsche Bank", raw={"line": line}))
    if not result:
        raise ImportErrorForUser("未能从该 PDF 识别交易。请确认它是 Deutsche Bank 交易流水。")
    return result


def nearest_date(lines: list[str], index: int):
    """Statement layouts often print an amount and its booking dates on nearby lines."""
    for distance in range(0, 10):
        for candidate in (index + distance, index - distance):
            if candidate < 0 or candidate >= len(lines):
                continue
            match = DATE_RE.search(lines[candidate])
            if match:
                return match
    return None


def parse_paypal_csv(content: bytes) -> list[ParsedTransaction]:
    rows = read_csv(content)
    result: list[ParsedTransaction] = []
    internal = ("bank deposit", "withdrawal", "authorization", "card deposit", "einzahlung", "abbuchung")
    for row in rows:
        normalized = normalize_keys(row)
        description = value(normalized, "description", "beschreibung")
        if any(word in description.lower() for word in internal):
            continue
        amount = parse_amount(value(normalized, "gross", "brutto", "net", "netto"))
        date_value = parse_date(value(normalized, "date", "datum"))
        if amount is None or not date_value:
            continue
        result.append(ParsedTransaction(
            date_value, amount, value(normalized, "currency", "wahrung", "währung") or "EUR",
            clean_merchant(value(normalized, "name", "to name") or description or "PayPal"), description,
            account="PayPal", external_id=value(normalized, "transaction id", "transaktionscode"), raw=row))
    return result


def parse_trade_republic_csv(content: bytes) -> list[ParsedTransaction]:
    rows = read_csv(content)
    result: list[ParsedTransaction] = []
    for row in rows:
        normalized = normalize_keys(row)
        description = value(normalized, "type", "transaction type", "vorgang", "beschreibung")
        amount = parse_amount(value(normalized, "amount", "value", "betrag", "cash amount", "wert"))
        date_value = parse_date(value(normalized, "date", "booking date", "datum", "wertstellung"))
        if amount is None or not date_value:
            continue
        merchant = clean_merchant(value(normalized, "name", "instrument", "isin") or description or "Trade Republic")
        result.append(ParsedTransaction(
            date_value, amount, value(normalized, "currency", "wahrung", "währung") or "EUR", merchant, description,
            account="Trade Republic", external_id=value(normalized, "reference", "id", "transaction id"), transaction_kind="investment", raw=row))
    return result


def read_csv(content: bytes) -> list[dict[str, str]]:
    text = ""
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise ImportErrorForUser("CSV 编码不受支持。")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def normalize_keys(row: dict[str, str]) -> dict[str, str]:
    return {re.sub(r"\s+", " ", (key or "").strip().lower()): (val or "").strip() for key, val in row.items()}


def value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if row.get(key):
            return row[key]
    return ""


def parse_date(raw: str):
    raw = raw.strip().split(" ")[0]
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(raw: str) -> Decimal | None:
    value = raw.replace("EUR", "").replace("€", "").replace(" ", "").strip()
    if not value:
        return None
    negative = value.endswith("-") or value.startswith("-")
    value = value.strip("+-")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        parsed = Decimal(value)
        return -parsed if negative else parsed
    except InvalidOperation:
        return None


def clean_merchant(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()[:160]
