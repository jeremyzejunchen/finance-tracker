from __future__ import annotations

import hashlib


def fingerprint_for_transaction(source_type: str, item: dict) -> str:
    parts = [
        source_type,
        item.get("source_format", ""),
        item.get("account", ""),
        item.get("booking_date", ""),
        str(item.get("amount_cents", "")),
        item.get("currency", ""),
        item.get("merchant", ""),
        item.get("external_id", ""),
        str(item.get("source_record_index", "")),
        item.get("source_record_key", ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
