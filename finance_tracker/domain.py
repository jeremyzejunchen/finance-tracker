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
        return {
            "token": self.token,
            "filename": self.filename,
            "source_type": self.source_type,
            "total": len(self.transactions),
            "eur_transactions": supported,
            "unsupported_currency": len(self.transactions) - supported,
            "warnings": self.warnings,
            "duplicate_source": self.duplicate_source,
            "sample": [item.serializable() for item in self.transactions[:12]],
        }
