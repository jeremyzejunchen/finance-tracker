from __future__ import annotations

import re

from ..config import FinanceTrackerConfig


IBAN_RE = re.compile(r"DE\d{20}")


def mark_internal_transfers(items: list[dict], config: FinanceTrackerConfig) -> None:
    own_ibans = config.own_ibans
    if not own_ibans:
        return
    for item in items:
        if item.get("is_internal_transfer"):
            continue
        details = f"{item.get('merchant', '')}\n{item.get('description', '')}\n{item.get('raw', {})}"
        found = {match.replace(" ", "").upper() for match in IBAN_RE.findall(details)}
        if found & own_ibans:
            item["is_internal_transfer"] = 1
            if not item.get("excluded_reason"):
                item["excluded_reason"] = "internal_transfer"
