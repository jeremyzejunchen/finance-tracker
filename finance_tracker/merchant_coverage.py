from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class MerchantBaselineRule:
    direction: str
    category: str
    keyword: str


def load_merchant_baseline(path: Path) -> list[MerchantBaselineRule]:
    direction = ""
    rules: list[MerchantBaselineRule] = []
    directions = {
        "## 固定支出": "expense",
        "## 活动支出": "expense",
        "### 汽车/交通": "expense",
        "## 收入": "income",
        "## 不参与统计的类别": "",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("### 汽车/交通"):
            direction = "expense"
            continue
        if line in directions:
            direction = directions[line]
            continue
        if not direction or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"子类", "三级子类"} or not cells[0].strip("-"):
            continue
        for keyword in cells[1].split(","):
            normalized = keyword.strip()
            if normalized and normalized != "见下方":
                rules.append(MerchantBaselineRule(direction, cells[0], normalized))
    return rules


def build_merchant_coverage(
    rows: Iterable[Mapping[str, object]],
    rules: Iterable[MerchantBaselineRule],
    infer_legacy_currency_exchange: bool = False,
    missing_currency: str = "UNKNOWN",
) -> dict:
    rules_by_direction: dict[str, list[MerchantBaselineRule]] = defaultdict(list)
    for rule in rules:
        rules_by_direction[rule.direction].append(rule)

    excluded_by_reason: Counter[str] = Counter()
    eligible_transactions = 0
    baseline_matched_transactions = 0
    date_values: list[str] = []
    amount_cents_by_currency: Counter[str] = Counter()
    for row in rows:
        excluded_reason = coverage_exclusion_reason(row, infer_legacy_currency_exchange)
        if excluded_reason:
            excluded_by_reason[excluded_reason] += 1
            continue
        eligible_transactions += 1
        booking_date = str(row.get("booking_date", ""))
        if booking_date:
            date_values.append(booking_date)
        currency = str(row.get("currency", "")).upper() or missing_currency
        amount_cents = amount_cents_for(row)
        amount_cents_by_currency[currency] += amount_cents
        direction = "income" if amount_cents >= 0 else "expense"
        merchant = merchant_for(row).casefold()
        if any(rule.keyword.casefold() in merchant for rule in rules_by_direction[direction]):
            baseline_matched_transactions += 1

    pending_review_transactions = eligible_transactions - baseline_matched_transactions
    coverage_percent = round(100 * baseline_matched_transactions / eligible_transactions, 2) if eligible_transactions else 0.0
    return {
        "eligible_transactions": eligible_transactions,
        "baseline_matched_transactions": baseline_matched_transactions,
        "pending_review_transactions": pending_review_transactions,
        "coverage_percent": coverage_percent,
        "excluded_by_reason": dict(sorted(excluded_by_reason.items())),
        "date_from": min(date_values) if date_values else "",
        "date_to": max(date_values) if date_values else "",
        "amount_cents_by_currency": dict(sorted(amount_cents_by_currency.items())),
    }


def coverage_exclusion_reason(row: Mapping[str, object], infer_legacy_currency_exchange: bool = False) -> str:
    if bool(row.get("is_failed_transaction")) or row.get("excluded_reason") == "failed_transaction":
        return "failed_transaction"
    transaction_kind = str(row.get("transaction_kind", ""))
    excluded_reason = str(row.get("excluded_reason", ""))
    if transaction_kind == "currency_exchange" or excluded_reason == "currency_exchange":
        return "currency_exchange"
    if infer_legacy_currency_exchange and merchant_for(row).upper().startswith("PAYM.ORDER"):
        return "currency_exchange"
    if bool(row.get("is_internal_transfer")) or excluded_reason == "internal_transfer":
        return "internal_transfer"
    return ""


def amount_cents_for(row: Mapping[str, object]) -> int:
    if "amount_cents" in row:
        return int(row["amount_cents"])
    try:
        return int(Decimal(str(row.get("amount", "0"))) * 100)
    except (InvalidOperation, ValueError):
        return 0


def merchant_for(row: Mapping[str, object]) -> str:
    return str(row.get("merchant") or row.get("merchant_normalized") or row.get("merchant_raw") or "")
