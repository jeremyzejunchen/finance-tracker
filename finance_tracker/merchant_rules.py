from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MerchantRule:
    canonical_merchant: str
    pattern: str
    match_kind: str
    direction: str
    category_id: int
    canonical_merchant_id: int | None = None


@dataclass(frozen=True, slots=True)
class MerchantResolution:
    canonical_merchant: str = ""
    canonical_merchant_id: int | None = None
    category_id: int | None = None
    category_status: str = "unclassified"
    category_reason: str = "unclassified"


class MerchantResolver:
    def __init__(self, rules: list[MerchantRule]):
        self.rules = rules

    def resolve(
        self,
        merchant: str,
        amount_cents: int,
        category_status: str,
        transaction_kind: str,
        excluded_reason: str,
    ) -> MerchantResolution:
        if category_status == "manual" or transaction_kind != "cash" or excluded_reason or amount_cents == 0:
            return MerchantResolution(category_status=category_status, category_reason="manual_override" if category_status == "manual" else "unclassified")
        direction = "income" if amount_cents > 0 else "expense"
        for match_kind in ("exact", "contains"):
            matches = [rule for rule in self.rules if rule.match_kind == match_kind and rule.direction == direction and self._matches(rule.pattern, merchant, match_kind)]
            unique_matches = {(rule.canonical_merchant, rule.category_id) for rule in matches}
            if len(unique_matches) == 1:
                rule = matches[0]
                return MerchantResolution(rule.canonical_merchant, rule.canonical_merchant_id, rule.category_id, "classified", f"rule_{match_kind}")
            if len(unique_matches) > 1:
                return MerchantResolution(category_reason=f"rule_conflict_{match_kind}")
        return MerchantResolution()

    @staticmethod
    def _matches(pattern: str, merchant: str, match_kind: str) -> bool:
        normalized_pattern = pattern.casefold().strip()
        normalized_merchant = merchant.casefold().strip()
        return normalized_merchant == normalized_pattern if match_kind == "exact" else normalized_pattern in normalized_merchant
