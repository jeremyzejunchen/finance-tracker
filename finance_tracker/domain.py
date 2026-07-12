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
    merchant: str
    description: str
    account: str = ""
    external_id: str = ""
    transaction_kind: str = "cash"
    value_date: date | None = None
    transaction_type: str = ""
    source_format: str = ""
    is_internal_transfer: bool = False
    is_failed_transaction: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def serializable(self) -> dict[str, Any]:
        data = asdict(self)
        data["booking_date"] = self.booking_date.isoformat()
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
        supported = sum(item.currency == "EUR" for item in self.transactions)
        dates = [item.booking_date for item in self.transactions]
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
            "warnings": self.warnings,
            "duplicate_source": self.duplicate_source,
            "sample": [item.serializable() for item in self.transactions[:12]],
        }
