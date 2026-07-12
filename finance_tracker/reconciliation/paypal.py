from __future__ import annotations

from datetime import date


def reconcile_paypal_rows(paypal_rows: list[dict], bank_rows: list[dict]) -> list[dict]:
    results: list[dict] = []
    for paypal in paypal_rows:
        candidates = [
            bank for bank in bank_rows
            if bank["amount_cents"] == paypal["amount_cents"]
            and abs((date.fromisoformat(bank["booking_date"]) - date.fromisoformat(paypal["booking_date"])).days) <= 5
            and "PAYPAL" in f"{bank['merchant']} {bank['description']}".upper()
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda bank: abs((date.fromisoformat(bank["booking_date"]) - date.fromisoformat(paypal["booking_date"])).days))
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
