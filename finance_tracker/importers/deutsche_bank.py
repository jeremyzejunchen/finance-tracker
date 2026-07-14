from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from ..cleaning.merchant import extract_merchant_from_payment_details, normalize_merchant
from ..domain import ParsedTransaction
from .common import ImportErrorForUser, parse_amount


DATE_F1_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
AMOUNT_F1_RE = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})*\.\d{2}$")
SKIP_LINES_F1 = {"", "EUR", "Booking date", "Value date", "Transactions Payment details", "Debit", "Credit", "Currency", "Booked transactions"}

AMOUNT_F2_RE = re.compile(r"^[+-]\s*\d{1,3}(?:[.,]\d{3})*[.,]\d{2}$")
DATE_PART_F2_RE = re.compile(r"^\d{2}-\d{2}-$")
YEAR_F2_RE = re.compile(r"^\d{4}$")
SKIP_F2_LINES = {"", "Credit", "Debit", "Item", "Value", "Booking", "date", "EUR", "IBAN", "of", "Page", "Statement"}
SKIP_F2_PREFIXES = (
    "0000000003", "Telephone", "24-hour", "January ", "February ", "March ", "April ", "May ", "June ", "July ",
    "August ", "September ", "October ", "November ", "December ", "Account statement", "Account holder", "Previous balance",
)
FALLBACK_WARNING = "Unknown Deutsche Bank PDF layout: parsed with generic fallback."
UNKNOWN_MERCHANT = "Unknown Deutsche Bank transaction"
MERCHANT_WARNING = "Unable to determine Deutsche Bank merchant from payment details."
GENERIC_MERCHANT_LABELS = {
    "payment reference/e2e-ref.", "payment reference", "e2e-ref.",
    "payment details", "reference", "karten", "kartenzahlung",
}


def extract_pdf_text(content: bytes) -> str:
    try:
        import fitz
    except ImportError as error:
        raise ImportErrorForUser("PDF 解析依赖未安装。") from error
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as error:
        raise ImportErrorForUser("无法读取 Deutsche Bank PDF 文件。") from error
    return "\n".join(page.get_text("text") for page in document)


def detect_layout(text: str) -> str:
    if "Booking date" in text and "Transactions Payment details" in text:
        return "db_transactions"
    if "Previous balance" in text or "Account statement" in text or "Kontoauszug" in text:
        return "db_account_statement"
    return "unknown"


def parse_pdf(content: bytes) -> tuple[list[ParsedTransaction], list[str]]:
    text = extract_pdf_text(content)
    return parse_text(text)


def parse_text(text: str) -> tuple[list[ParsedTransaction], list[str]]:
    layout = detect_layout(text)
    if layout == "db_transactions":
        return parse_transactions_layout(text), []
    if layout == "db_account_statement":
        return parse_account_statement_layout(text), []
    records = parse_generic_fallback(text)
    if not records:
        raise ImportErrorForUser("未能从该 Deutsche Bank PDF 识别交易。")
    return records, [FALLBACK_WARNING]


def parse_transactions_layout(text: str) -> list[ParsedTransaction]:
    lines = text.splitlines()
    transactions: list[ParsedTransaction] = []
    i = 0
    record_index = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line in SKIP_LINES_F1:
            continue
        if any(token in line for token in ("https://", "Customer number", "Created on", "Sorted by", "Old balance")):
            continue
        if line.startswith("Page ") and "of" in line:
            continue
        if not DATE_F1_RE.match(line):
            continue
        booking_raw = line
        if i >= len(lines):
            break
        value_raw = lines[i].strip()
        if DATE_F1_RE.match(value_raw):
            i += 1
        else:
            value_raw = booking_raw

        description_lines: list[str] = []
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate:
                i += 1
                continue
            if AMOUNT_F1_RE.match(candidate):
                break
            if DATE_F1_RE.match(candidate) or candidate in SKIP_LINES_F1:
                break
            if any(token in candidate for token in ("https://", "Customer number", "Page ")):
                break
            description_lines.append(candidate)
            i += 1
        if i >= len(lines) or not AMOUNT_F1_RE.match(lines[i].strip()) or not description_lines:
            continue
        amount = Decimal(lines[i].strip().replace(",", ""))
        i += 1
        detail_lines: list[str] = []
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate:
                i += 1
                continue
            if candidate == "EUR":
                i += 1
                continue
            if DATE_F1_RE.match(candidate) or candidate in SKIP_LINES_F1:
                break
            if any(token in candidate for token in ("https://", "Customer number", "Page ", "Booked transactions", "Sorted by")):
                break
            detail_lines.append(candidate)
            i += 1
        type_line = description_lines[0]
        transaction_type = extract_type_f1(type_line)
        merchant_raw = extract_merchant_f1(type_line, description_lines[1:] + detail_lines)
        booking_date = datetime.strptime(booking_raw, "%m/%d/%Y").date()
        value_date = datetime.strptime(value_raw, "%m/%d/%Y").date()
        transactions.append(
            ParsedTransaction(
                booking_date=booking_date,
                value_date=value_date,
                amount=amount,
                currency="EUR",
                merchant_raw=merchant_raw,
                merchant_normalized=normalize_merchant(merchant_raw),
                description_raw="\n".join(description_lines[1:] + detail_lines),
                account="Deutsche Bank",
                transaction_type=transaction_type,
                source_format="db_transactions",
                source_record_index=record_index,
                source_record_key=f"db_transactions:{record_index}",
                raw={"layout": "db_transactions", "type_line": type_line, "detail_lines": detail_lines},
            )
        )
        record_index += 1
    return transactions


def parse_account_statement_layout(text: str) -> list[ParsedTransaction]:
    lines = text.splitlines()
    transactions: list[ParsedTransaction] = []
    i = 0
    record_index = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if line in SKIP_F2_LINES:
            continue
        if not line or any(line.startswith(prefix) for prefix in SKIP_F2_PREFIXES):
            if "Previous balance" in line:
                while i < len(lines) and not lines[i].strip():
                    i += 1
                if i < len(lines) and AMOUNT_F2_RE.match(lines[i].strip()):
                    i += 1
            continue
        if not AMOUNT_F2_RE.match(line):
            continue
        amount = parse_amount(line)
        if amount is None:
            continue
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        type_line = lines[i].strip()
        i += 1
        merchant_lines: list[str] = []
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate:
                i += 1
                continue
            if DATE_PART_F2_RE.match(candidate) or AMOUNT_F2_RE.match(candidate):
                break
            merchant_lines.append(candidate)
            i += 1
        value_date, i = read_split_date(lines, i)
        booking_date, i = read_split_date(lines, i)
        details_lines: list[str] = []
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate:
                i += 1
                continue
            if AMOUNT_F2_RE.match(candidate) or DATE_PART_F2_RE.match(candidate):
                break
            details_lines.append(candidate)
            i += 1
        if not booking_date:
            continue
        merchant_raw = extract_merchant_f2(type_line, merchant_lines, details_lines)
        description_raw = "\n".join(merchant_lines[1:] + details_lines)
        normalized_type = norm_type_f2(type_line)
        raw_blob = " ".join([type_line] + merchant_lines + details_lines)
        warnings = [MERCHANT_WARNING] if merchant_raw == UNKNOWN_MERCHANT else []
        transactions.append(
            ParsedTransaction(
                booking_date=booking_date,
                value_date=value_date or booking_date,
                amount=amount,
                currency="EUR",
                merchant_raw=merchant_raw,
                merchant_normalized=normalize_deutsche_bank_merchant(merchant_raw),
                description_raw=description_raw,
                account="Deutsche Bank",
                transaction_type=normalized_type,
                source_format="db_account_statement",
                source_record_index=record_index,
                source_record_key=f"db_account_statement:{record_index}",
                is_internal_transfer=is_internal_transfer_f2(type_line, merchant_lines, details_lines),
                is_failed_transaction=is_failed_transaction_f2(raw_blob),
                raw={"layout": "db_account_statement", "type_line": type_line, "merchant_lines": merchant_lines, "details_lines": details_lines},
                warnings=warnings,
            )
        )
        record_index += 1
    return transactions


def parse_generic_fallback(text: str) -> list[ParsedTransaction]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    results: list[ParsedTransaction] = []
    record_index = 0
    date_re = re.compile(r"(?P<date>\d{2}[./-]\d{2}[./-]\d{2,4})")
    amount_re = re.compile(r"(?P<amount>[+-]?\s?\d{1,3}(?:[. ]\d{3})*[,\.]\d{2})")
    for index, line in enumerate(lines):
        amounts = list(amount_re.finditer(line))
        if not amounts:
            continue
        amount = parse_amount(amounts[-1].group("amount"))
        date_match = date_re.search(line)
        if amount is None or not date_match:
            continue
        booking_date = parse_date_fallback(date_match.group("date"))
        if booking_date is None:
            continue
        merchant_raw = line[:amounts[-1].start()].strip(" -") or "Unknown Deutsche Bank transaction"
        results.append(
            ParsedTransaction(
                booking_date=booking_date,
                value_date=booking_date,
                amount=amount,
                currency="EUR",
                merchant_raw=merchant_raw,
                merchant_normalized=normalize_merchant(merchant_raw),
                description_raw=" ".join(lines[index + 1:index + 5]),
                account="Deutsche Bank",
                source_format="db_fallback",
                source_record_index=record_index,
                source_record_key=f"db_fallback:{record_index}",
                raw={"line": line},
                warnings=[FALLBACK_WARNING],
            )
        )
        record_index += 1
    return results


def extract_type_f1(line: str) -> str:
    for token in ("SEPA-Direct Debit", "Debit Card Payment", "SEPA Transfer", "Dauerauftrag", "Gutschrift"):
        if token in line:
            return token
    return line.split("  ")[0].strip()


def extract_merchant_f1(first_line: str, rest: list[str]) -> str:
    for prefix in ("SEPA-Direct Debit ", "Debit Card Payment ", "SEPA Transfer ", "Dauerauftrag ", "Gutschrift "):
        if prefix in first_line:
            name = first_line.replace(prefix, "").strip()
            if name:
                return name
            break
    return extract_merchant_from_payment_details(rest) or first_line.strip()


def extract_merchant_f2(type_line: str, merchant_lines: list[str], details_lines: list[str] | None = None) -> str:
    transaction_type = norm_type_f2(type_line)
    explicit = extract_explicit_retailer([*merchant_lines, *(details_lines or [])], transaction_type)
    if explicit:
        return explicit
    prefixes = (
        "SEPA Lastschrifteinzug von ", "SEPA Überweisung an ", "SEPA Überweisung von ", "SEPA Echtzeitüberweisung an ",
        "SEPA Echtzeitüberweisung von ", "Echtzeitüberweisung an ", "Echtzeitüberweisung von ", "Dauerauftrag an ", "Gutschrift von ",
    )
    for prefix in prefixes:
        if type_line.startswith(prefix):
            name = type_line[len(prefix):].strip()
            if name and not is_generic_merchant_label(name):
                return normalize_merchant(name)
            break
    fallback = merchant_lines[0].strip() if merchant_lines else type_line.strip()
    if is_generic_merchant_label(fallback):
        return UNKNOWN_MERCHANT
    return normalize_deutsche_bank_merchant(fallback)


def is_generic_merchant_label(value: str) -> bool:
    normalized = normalize_merchant(value).casefold()
    if normalized in GENERIC_MERCHANT_LABELS:
        return True
    return bool(re.match(r"^(?:payment reference/e2e-ref\.|payment reference|e2e-ref\.)(?:\s|$)", normalized))


def extract_explicit_retailer(lines: list[str], transaction_type: str) -> str:
    for line in lines:
        text = normalize_merchant(line)
        einkauf = re.search(r"\bEinkauf bei\s+(.+)$", text, re.IGNORECASE)
        if einkauf:
            candidate = normalize_merchant(einkauf.group(1))
            if not is_generic_merchant_label(candidate):
                return candidate
        retailer_message = re.match(r"^((?:ALDI|LIDL))\s+sagt\s+Danke\b", text, re.IGNORECASE)
        if retailer_message:
            return normalize_merchant(retailer_message.group(1))
        if transaction_type == "Debit Card Payment":
            card_match = re.match(
                r"^(.+?)//.+\s+\d{2}-\d{2}-\d{4}T\d{2}:\d{2}:\d{2}\s+Karten$",
                text,
            )
            if card_match:
                candidate = normalize_merchant(card_match.group(1))
                if not is_generic_merchant_label(candidate):
                    return candidate
    if transaction_type == "Debit Card Payment":
        card_text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        card_match = re.search(r"(.+?)//.+\d{2}-\d{2}-\d{4}.+?Karten", card_text, re.IGNORECASE)
        if card_match:
            candidate = normalize_merchant(card_match.group(1))
            if not is_generic_merchant_label(candidate):
                return candidate
    return ""


def normalize_deutsche_bank_merchant(raw: str) -> str:
    value = normalize_merchant(raw)
    rules = (
        (r"\bGO\s+ASIA\b", "GO ASIA"),
        (r"\bKAUFLAND\b", "KAUFLAND"),
        (r"\bTEGUT\b", "TEGUT"),
        (r"\bALDI(?:\s+(?:NORD|SÜD))?\b", "ALDI"),
        (r"\bLIDL\b", "LIDL"),
        (r"\bdm[- ]drogerie\s+markt\b", "dm-drogerie markt"),
    )
    for pattern, canonical in rules:
        if re.search(pattern, value, re.IGNORECASE):
            return canonical
    return value


def norm_type_f2(line: str) -> str:
    if "Lastschrifteinzug" in line:
        return "SEPA-Direct Debit"
    if "Überweisung an" in line:
        return "SEPA Transfer (out)"
    if "Überweisung von" in line:
        return "SEPA Transfer (in)"
    if "Kartenzahlung" in line:
        return "Debit Card Payment"
    if "Gutschrift" in line:
        return "Credit"
    if "Dauerauftrag" in line:
        return "Standing Order"
    return line


def read_split_date(lines: list[str], index: int):
    if index + 1 < len(lines) and DATE_PART_F2_RE.match(lines[index].strip()) and YEAR_F2_RE.match(lines[index + 1].strip()):
        return datetime.strptime(lines[index].strip() + lines[index + 1].strip(), "%d-%m-%Y").date(), index + 2
    return None, index


def parse_date_fallback(raw: str):
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def is_internal_transfer_f2(type_line: str, merchant_lines: list[str], details_lines: list[str]) -> bool:
    blob = " ".join([type_line] + merchant_lines + details_lines).upper()
    return "EIGENKONTO" in blob


def is_failed_transaction_f2(raw_blob: str) -> bool:
    candidate = raw_blob.upper()
    return any(token in candidate for token in ("RETURN", "RÜCKGABE", "RUECKGABE", "FAILED", "CHARGEBACK"))
