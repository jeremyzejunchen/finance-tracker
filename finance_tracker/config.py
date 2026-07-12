from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def default_config_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "FinanceTracker" / "config.json"


@dataclass(slots=True)
class FinanceTrackerConfig:
    own_accounts: list[dict[str, str]] = field(default_factory=list)
    paypal_accounts: list[dict[str, object]] = field(default_factory=list)

    @property
    def own_ibans(self) -> set[str]:
        values: set[str] = set()
        for account in self.own_accounts:
            iban = str(account.get("iban", "")).replace(" ", "").upper()
            if iban:
                values.add(iban)
        return values

    def paypal_account_for(self, filename: str, sender_email: str) -> str:
        stem = Path(filename).stem.lower()
        sender = sender_email.strip().lower()
        for item in self.paypal_accounts:
            account = str(item.get("account", "")).strip()
            if not account:
                continue
            if sender and sender in {str(value).strip().lower() for value in item.get("sender_emails", [])}:
                return account
            if any(token and token.lower() in stem for token in item.get("filename_contains", [])):
                return account
        return "PayPal"


def load_config(path: Path | None = None) -> FinanceTrackerConfig:
    target = path or default_config_path()
    if not target.is_file():
        return FinanceTrackerConfig()
    raw = json.loads(target.read_text(encoding="utf-8"))
    return FinanceTrackerConfig(
        own_accounts=list(raw.get("own_accounts", [])),
        paypal_accounts=list(raw.get("paypal_accounts", [])),
    )
