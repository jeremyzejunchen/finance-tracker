from __future__ import annotations

from datetime import date


def paypal_bank_date_difference(paypal: dict, bank: dict) -> int:
    return abs((date.fromisoformat(bank["booking_date"]) - date.fromisoformat(paypal["booking_date"])).days)


def paypal_bank_candidate(paypal: dict, bank: dict) -> bool:
    if paypal["amount_cents"] != bank["amount_cents"]:
        return False
    if paypal_bank_date_difference(paypal, bank) > 5:
        return False
    return "PAYPAL" in f"{bank.get('merchant', '')} {bank.get('description', '')}".upper()


def reconcile_paypal_rows(paypal_rows: list[dict], bank_rows: list[dict]) -> list[dict]:
    results: list[dict] = []
    for paypal in paypal_rows:
        candidates = [
            bank for bank in bank_rows
            if paypal_bank_candidate(paypal, bank)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda bank: paypal_bank_date_difference(paypal, bank))
        confidence = 0.95 if len(candidates) == 1 else 0.60
        reason = "paypal_purchase_to_bank_debit" if paypal["amount_cents"] < 0 else "paypal_payout_to_bank_credit"
        results.append({
            "paypal_id": paypal["id"],
            "bank_id": candidates[0]["id"],
            "reason": reason,
            "confidence": confidence,
            "status": "automatic" if confidence >= 0.9 else "suggested",
        })
    return results
