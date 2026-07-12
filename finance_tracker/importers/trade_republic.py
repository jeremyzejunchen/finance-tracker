from __future__ import annotations

import re

from ..cleaning.merchant import normalize_merchant
from ..config import FinanceTrackerConfig
from ..domain import ParsedTransaction
from .common import ImportErrorForUser, normalize_keys, parse_amount, parse_date, read_csv, value


def parse_trade_republic_csv(content: bytes, config: FinanceTrackerConfig) -> list[ParsedTransaction]:
    rows = read_csv(content)
    result: list[ParsedTransaction] = []
    for index, row in enumerate(rows):
        normalized = normalize_keys(row)
        if value(normalized, "category").upper() != "CASH":
            continue
        booking_date = parse_date(value(normalized, "date", "booking date", "datum", "wertstellung"))
        amount = parse_amount(value(normalized, "amount", "value", "betrag", "cash amount", "wert"))
        if booking_date is None or amount is None:
            continue
        transaction_type = value(normalized, "type", "transaction type", "vorgang")
        description = value(normalized, "description", "beschreibung") or transaction_type
        counterparty_name = value(normalized, "counterparty_name", "name", "instrument", "isin")
        counterparty_iban = value(normalized, "counterparty_iban").replace(" ", "").upper()
        merchant_raw = extract_tr_merchant(transaction_type, description, counterparty_name)
        is_internal = (
            "TRANSFER" in transaction_type
            and "DIRECT_DEBIT" not in transaction_type
            and counterparty_iban in config.own_ibans
        )
        result.append(
            ParsedTransaction(
                booking_date=booking_date,
                value_date=booking_date,
                amount=amount,
                currency=value(normalized, "currency", "wahrung", "währung") or "EUR",
                merchant_raw=merchant_raw,
                merchant_normalized=normalize_merchant(merchant_raw),
                description_raw=description,
                account="Trade Republic",
                external_id=value(normalized, "transaction_id", "reference", "id", "transaction id"),
                transaction_type=transaction_type,
                source_format="trade_republic_csv",
                source_record_index=index,
                source_record_key=value(normalized, "transaction_id", "reference", "id", "transaction id") or f"tr:{index}",
                is_internal_transfer=is_internal,
                raw=dict(row),
            )
        )
    if not result:
        raise ImportErrorForUser("未能从该 Trade Republic CSV 识别现金交易。")
    return result


def extract_tr_merchant(transaction_type: str, description: str, fallback_name: str) -> str:
    if transaction_type == "TRANSFER_DIRECT_DEBIT_INBOUND":
        match = re.search(r"transfer to (.+?) \(", description, re.I)
        if match:
            return match.group(1)
    if transaction_type == "CARD_SUCCESSFUL_TRANSACTION":
        match = re.search(r"card successful transaction\s+(.+)", description, re.I)
        if match:
            return match.group(1)
    return fallback_name or description or "Trade Republic"
