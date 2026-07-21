from __future__ import annotations

import csv
import io
from pathlib import Path

from ..cleaning.merchant import normalize_merchant
from ..domain import ParsedTransaction
from .common import ImportErrorForUser, normalize_keys, parse_amount, parse_date, value


REQUIRED_HEADERS = {"buchungstag", "betrag", "währung"}
GENERIC_MERCHANTS = {"sepa-lastschrift", "lastschrift", "überweisung", "sepa-überweisung", "gutschrift"}


def is_kontoumsaetze_csv(filename: str, content: bytes) -> bool:
    return bool(is_kontoumsaetze_filename(filename) and _header_row(content) is not None)


def parse_kontoumsaetze_csv(content: bytes, filename: str) -> list[ParsedTransaction]:
    header_index = _header_row(content)
    if header_index is None:
        raise ImportErrorForUser("Kontoumsaetze CSV 缺少必需交易表头。")

    text = _decode_utf8(content)
    try:
        rows = csv.DictReader(io.StringIO("\n".join(text.splitlines()[header_index:])), delimiter=";", strict=True)
        result = [_parse_row(row, index) for index, row in enumerate(rows)]
    except csv.Error as error:
        raise ImportErrorForUser("Kontoumsaetze CSV 格式无效。") from error
    transactions = [item for item in result if item is not None]
    if not transactions:
        raise ImportErrorForUser("未能从该 Kontoumsaetze CSV 识别可导入交易。")
    return transactions


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ImportErrorForUser("Kontoumsaetze CSV 必须使用 UTF-8 编码。") from error


def _header_row(content: bytes) -> int | None:
    try:
        lines = _decode_utf8(content).splitlines()
    except ImportErrorForUser:
        return None
    for index, line in enumerate(lines):
        if ";" not in line:
            continue
        headers = normalize_keys({header: "" for header in next(csv.reader([line], delimiter=";"))})
        if REQUIRED_HEADERS.issubset(headers):
            return index
    return None


def is_kontoumsaetze_filename(filename: str) -> bool:
    name = Path(filename).name
    return name.startswith("Kontoumsaetze") and name.endswith("-czj.csv")


def _parse_row(row: dict[str, str | list[str] | None], index: int) -> ParsedTransaction | None:
    if any(isinstance(item, list) for item in row.values()):
        raise ImportErrorForUser("Kontoumsaetze CSV 包含无效列。")
    normalized = normalize_keys({key or "": item or "" for key, item in row.items()})
    booking_date = parse_date(value(normalized, "buchungstag"))
    amount = parse_amount(value(normalized, "betrag"))
    if booking_date is None or amount is None:
        return None
    transaction_type = value(normalized, "buchungstext")
    counterparty = value(normalized, "begünstigter / auftraggeber")
    purpose = value(normalized, "verwendungszweck")
    merchant_raw = counterparty if counterparty.casefold() not in GENERIC_MERCHANTS else purpose
    merchant_raw = merchant_raw or purpose or transaction_type or "Kontoumsaetze"
    return ParsedTransaction(
        booking_date=booking_date,
        value_date=parse_date(value(normalized, "wert", "wertstellung", "valutadatum")) or booking_date,
        amount=amount,
        currency=value(normalized, "währung") or "EUR",
        merchant_raw=merchant_raw,
        merchant_normalized=normalize_merchant(merchant_raw),
        description_raw=purpose,
        account="ME",
        transaction_type=transaction_type,
        source_format="kontoumsaetze_csv",
        source_record_index=index,
        source_record_key=f"kontoumsaetze:{index}",
        raw={
            "booking_date": booking_date.isoformat(),
            "value_date": (parse_date(value(normalized, "wert", "wertstellung", "valutadatum")) or booking_date).isoformat(),
            "amount": str(amount),
            "currency": value(normalized, "währung") or "EUR",
            "transaction_type": transaction_type,
        },
    )
