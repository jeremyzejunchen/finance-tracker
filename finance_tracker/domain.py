from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class ParsedTransaction:
    booking_date: date
    amount: Decimal
    currency: str
    merchant_raw: str
    merchant_normalized: str
    description_raw: str
    account: str = ""
    external_id: str = ""
    transaction_kind: str = "cash"
    value_date: date | None = None
    transaction_type: str = ""
    source_format: str = ""
    source_record_index: int = 0
    source_record_key: str = ""
    is_internal_transfer: bool = False
    is_failed_transaction: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def serializable(self) -> dict[str, Any]:
        data = asdict(self)
        data["booking_date"] = self.booking_date.isoformat()
        if self.value_date:
            data["value_date"] = self.value_date.isoformat()
        data["amount"] = str(self.amount)
        return data


@dataclass(slots=True)
class ImportPreview:
    token: str
    filename: str
    source_type: str
    file_hash: str
    transactions: list[ParsedTransaction]
    warnings: list[str]
    duplicate_source: bool = False

    def summary(self) -> dict[str, Any]:
        supported = sum(item.currency.upper() == "EUR" for item in self.transactions)
        dates = [item.booking_date for item in self.transactions]
        warning_count = len(self.warnings) + sum(len(item.warnings) for item in self.transactions)
        return {
            "token": self.token,
            "filename": self.filename,
            "source_type": self.source_type,
            "total": len(self.transactions),
            "eur_transactions": supported,
            "unsupported_currency": len(self.transactions) - supported,
            "date_from": min(dates).isoformat() if dates else "",
            "date_to": max(dates).isoformat() if dates else "",
            "income_cents": sum(int(item.amount * 100) for item in self.transactions if item.currency == "EUR" and item.amount > 0),
            "expense_cents": sum(int(item.amount * 100) for item in self.transactions if item.currency == "EUR" and item.amount < 0),
            "warning_count": warning_count,
            "warnings": self.warnings,
            "duplicate_source": self.duplicate_source,
            "sample": [item.serializable() for item in self.transactions[:12]],
        }

    def details(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.transactions:
            rows.append({
                "preview_token": self.token,
                "filename": self.filename,
                "source_type": self.source_type,
                "source_record_index": item.source_record_index,
                "booking_date": item.booking_date.isoformat(),
                "value_date": (item.value_date or item.booking_date).isoformat(),
                "amount": str(item.amount),
                "currency": item.currency,
                "merchant_raw": item.merchant_raw,
                "merchant_normalized": item.merchant_normalized,
                "description_raw": item.description_raw,
                "account": item.account,
                "external_id": item.external_id,
                "transaction_kind": item.transaction_kind,
                "transaction_type": item.transaction_type,
                "source_format": item.source_format,
                "is_internal_transfer": item.is_internal_transfer,
                "is_failed_transaction": item.is_failed_transaction,
                "warnings": list(item.warnings),
                "duplicate_source": self.duplicate_source,
            })
        return rows
