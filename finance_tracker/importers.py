from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .domain import ParsedTransaction


DATE_RE = re.compile(r"(?P<date>\d{2}[./-]\d{2}[./-]\d{2,4})")
AMOUNT_RE = re.compile(r"(?P<amount>[+-]?\s?\d{1,3}(?:[. ]\d{3})*[,\.]\d{2})")
OWN_IBANS = {"DE64290700240344376900", "DE79100123455797203011", "DE08100123456340785111"}


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
    try:
        import fitz
    except ImportError as error:
        raise ImportErrorForUser("PDF 解析组件未安装，请安装项目依赖后重试。") from error
    document = fitz.open(stream=content, filetype="pdf")
    text = "\n".join(page.get_text("text") for page in document)
    specialized = parse_db_transactions_layout(text) if "Booking date" in text else parse_db_statement_layout(text)
    if specialized:
        return specialized
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


def parse_db_transactions_layout(text: str) -> list[ParsedTransaction]:
    date_re = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    amount_re = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})*\.\d{2}$")
    lines = [line.strip() for line in text.splitlines()]
    result = []; i = 0
    while i < len(lines):
        if not date_re.match(lines[i]): i += 1; continue
        booking = lines[i]; i += 1
        value_date = lines[i] if i < len(lines) and date_re.match(lines[i]) else booking
        if value_date != booking: i += 1
        description = []
        while i < len(lines) and not amount_re.match(lines[i]) and not date_re.match(lines[i]):
            if lines[i] and lines[i] not in {"EUR", "Debit", "Credit", "Currency"}: description.append(lines[i])
            i += 1
        if i >= len(lines) or not amount_re.match(lines[i]): continue
        amount = Decimal(lines[i].replace(",", "")); i += 1
        if not description: continue
        kind = description[0]
        merchant = re.sub(r"^(SEPA-Direct Debit|Debit Card Payment|SEPA Transfer|Dauerauftrag|Gutschrift)\s*", "", kind).strip()
        details = " ".join(description[1:])
        if not merchant:
            match = re.search(r"Payment details\s+(.+?)/", details)
            merchant = match.group(1).strip() if match else (description[1] if len(description) > 1 else kind)
        bd = datetime.strptime(booking, "%m/%d/%Y").date(); vd = datetime.strptime(value_date, "%m/%d/%Y").date()
        result.append(ParsedTransaction(bd, amount, "EUR", clean_merchant(merchant), details, account="Deutsche Bank",
                         value_date=vd, transaction_type=kind, source_format="db_f1", raw={"layout":"f1"}))
    return result


def parse_db_statement_layout(text: str) -> list[ParsedTransaction]:
    amount_re = re.compile(r"^[+-]\s*\d{1,3}(?:[.,]\d{3})*[.,]\d{2}$")
    date_part = re.compile(r"^\d{2}-\d{2}-$"); year_re = re.compile(r"^\d{4}$")
    lines = [line.strip() for line in text.splitlines()]
    result = []; i = 0
    def read_date(pos):
        if pos + 1 < len(lines) and date_part.match(lines[pos]) and year_re.match(lines[pos + 1]):
            return datetime.strptime(lines[pos] + lines[pos + 1], "%d-%m-%Y").date(), pos + 2
        return None, pos
    while i < len(lines):
        if not amount_re.match(lines[i]): i += 1; continue
        amount = parse_amount(lines[i]); i += 1
        while i < len(lines) and not lines[i]: i += 1
        if i >= len(lines): break
        kind = lines[i]; i += 1; merchants = []
        while i < len(lines) and not date_part.match(lines[i]) and not amount_re.match(lines[i]):
            if lines[i]: merchants.append(lines[i])
            i += 1
        value_date, i = read_date(i); booking_date, i = read_date(i)
        if not booking_date or amount is None: continue
        prefixes = ("SEPA Lastschrifteinzug von ", "SEPA Überweisung an ", "SEPA Überweisung von ", "SEPA Echtzeitüberweisung an ", "SEPA Echtzeitüberweisung von ", "Echtzeitüberweisung an ", "Echtzeitüberweisung von ", "Dauerauftrag an ", "Gutschrift von ")
        merchant = kind
        for prefix in prefixes:
            if kind.startswith(prefix): merchant = kind[len(prefix):].strip() or (merchants[0] if merchants else kind); break
        else:
            if merchants: merchant = merchants[0]
        details = " ".join(merchants[1:])
        if merchant == "Payment Reference/E2E-Ref." and merchants:
            merchant = merchants[0].split("/")[0]
        internal = merchant.startswith("PAYM.ORDER")
        result.append(ParsedTransaction(booking_date, amount, "EUR", clean_merchant(merchant), details, account="Deutsche Bank",
                         value_date=value_date or booking_date, transaction_type=kind, source_format="db_f2",
                         is_internal_transfer=internal, raw={"layout":"f2"}))
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
    internal = (
        "bank deposit to pp account", "general card deposit", "general card withdrawal",
        "user initiated withdrawal", "reversal of ach deposit", "reversal of ach withdrawal",
        "account hold for open authorization", "reversal of general account hold",
        "bankgutschrift auf paypal-konto", "allgemeine gutschrift auf kreditkarte",
        "von nutzer eingeleitete abbuchung", "rückbuchung von ach-gutschrift",
        "einbehaltung für offene autorisierung", "rückbuchung allgemeiner einbehaltung",
        "ach-überweisung als zahlungsquelle für ausgleich von kontoguthaben",
    )
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
    if not result:
        raise ImportErrorForUser("未能从该 PayPal CSV 识别可导入的交易。")
    return result


def parse_trade_republic_csv(content: bytes) -> list[ParsedTransaction]:
    rows = read_csv(content)
    result: list[ParsedTransaction] = []
    for row in rows:
        normalized = normalize_keys(row)
        if value(normalized, "category").upper() != "CASH":
            continue
        description = value(normalized, "type", "transaction type", "vorgang", "beschreibung")
        amount = parse_amount(value(normalized, "amount", "value", "betrag", "cash amount", "wert"))
        date_value = parse_date(value(normalized, "date", "booking date", "datum", "wertstellung"))
        if amount is None or not date_value:
            continue
        transaction_type = value(normalized, "type", "transaction type")
        details = value(normalized, "description", "beschreibung")
        merchant = clean_merchant(value(normalized, "name", "counterparty_name", "instrument", "isin") or description or "Trade Republic")
        if transaction_type == "TRANSFER_DIRECT_DEBIT_INBOUND":
            match = re.search(r"transfer to (.+?) \(", details, re.I)
            if match:
                merchant = clean_merchant(match.group(1))
        counterparty_iban = value(normalized, "counterparty_iban").replace(" ", "")
        internal = "TRANSFER" in transaction_type and "DIRECT_DEBIT" not in transaction_type and counterparty_iban in OWN_IBANS
        result.append(ParsedTransaction(
            date_value, amount, value(normalized, "currency", "wahrung", "währung") or "EUR", merchant, details or description,
            account="Trade Republic", external_id=value(normalized, "transaction_id", "reference", "id", "transaction id"),
            transaction_kind="cash", value_date=date_value, transaction_type=transaction_type, source_format="tr_csv",
            is_internal_transfer=internal, raw=row))
    if not result:
        raise ImportErrorForUser("未能从该 CSV 识别交易。请确认文件格式和表头。")
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
