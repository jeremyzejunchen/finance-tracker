from __future__ import annotations

from datetime import date


def mark_refund_pairs(items: list[dict]) -> None:
    unmatched = set(range(len(items)))
    for i, left in enumerate(items):
        if i not in unmatched:
            continue
        for j in list(unmatched):
            if j <= i:
                continue
            right = items[j]
            if left["amount_cents"] + right["amount_cents"] != 0:
                continue
            if abs((_from_iso(left["booking_date"]) - _from_iso(right["booking_date"])).days) > 3:
                continue
            if token_overlap(left["merchant"], right["merchant"]) or "refund" in f"{left['description']} {right['description']}".lower():
                left["excluded_reason"] = "matched_refund_pair"
                right["excluded_reason"] = "matched_refund_pair"
                unmatched.discard(i)
                unmatched.discard(j)
                break


def _from_iso(raw: str) -> date:
    return date.fromisoformat(raw)


def token_overlap(left: str, right: str) -> bool:
    import re

    a = {part for part in re.findall(r"\w+", left.lower()) if len(part) > 3}
    b = {part for part in re.findall(r"\w+", right.lower()) if len(part) > 3}
    return bool(a & b)
