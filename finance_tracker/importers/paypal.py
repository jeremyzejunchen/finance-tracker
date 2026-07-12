from __future__ import annotations

from ..cleaning.merchant import normalize_merchant
from ..config import FinanceTrackerConfig
from ..domain import ParsedTransaction
from .common import ImportErrorForUser, normalize_keys, parse_amount, parse_date, read_csv, value


PAYPAL_INTERNAL_TYPES = {
    "Bank Deposit to PP Account",
    "General Card Deposit",
    "General Card Withdrawal",
    "User Initiated Withdrawal",
    "Reversal of ACH Deposit",
    "Reversal of ACH Withdrawal Transaction",
    "Account Hold for Open Authorization",
    "Reversal of General Account Hold",
    "General Authorization",
    "Bankgutschrift auf PayPal-Konto",
    "Allgemeine Gutschrift auf Kreditkarte",
    "Von Nutzer eingeleitete Abbuchung",
    "Rückbuchung von ACH-Gutschrift",
    "Einbehaltung für offene Autorisierung",
    "Rückbuchung allgemeiner Einbehaltung",
    "ACH-Überweisung als Zahlungsquelle für Ausgleich von Kontoguthaben",
    "Allgemeine Autorisierung",
}

PAYPAL_TYPE_DE_TO_EN = {
    "PayPal Express-Zahlung": "Express Checkout Payment",
    "Handyzahlung": "Mobile Payment",
    "Allgemeine Zahlung": "General Payment",
    "Zahlung im Einzugsverfahren mit Zahlungsrechnung": "PreApproved Payment Bill User Payment",
}

PAYPAL_SUPPRESSED_KEYWORDS = (
    "withdrawal", "abbuchung", "authorization", "autorisierung", "hold", "einbehaltung",
    "deposit", "gutschrift", "reversal", "rückbuchung",
)


def parse_paypal_csv(content: bytes, filename: str, config: FinanceTrackerConfig) -> list[ParsedTransaction]:
    rows = read_csv(content)
    result: list[ParsedTransaction] = []
    sender_email = find_sender_email(rows)
    account = config.paypal_account_for(filename, sender_email)
    for index, row in enumerate(rows):
        normalized = normalize_keys(row)
        description = PAYPAL_TYPE_DE_TO_EN.get(value(normalized, "beschreibung"), value(normalized, "description", "beschreibung"))
        if not description:
            description = value(normalized, "description", "beschreibung")
        if is_internal_paypal_row(description):
            continue
        amount = parse_amount(value(normalized, "gross", "brutto", "net", "netto"))
        booking_date = parse_date(value(normalized, "date", "datum"))
        if amount is None or not booking_date:
            continue
        merchant_raw = value(normalized, "name", "to name") or description or "PayPal"
        external_id = value(normalized, "transaction id", "transaktionscode")
        raw_email = value(normalized, "from email address", "absender e-mail-adresse")
        transaction_type = description
        raw_payload = dict(row)
        raw_payload["matched_sender_email"] = raw_email
        result.append(
            ParsedTransaction(
                booking_date=booking_date,
                value_date=booking_date,
                amount=amount,
                currency=value(normalized, "currency", "wahrung", "währung") or "EUR",
                merchant_raw=merchant_raw,
                merchant_normalized=normalize_merchant(merchant_raw),
                description_raw=description,
                account=account,
                external_id=external_id,
                transaction_type=transaction_type,
                source_format="paypal_csv",
                source_record_index=index,
                source_record_key=external_id or f"{filename}:{index}",
                raw=raw_payload,
            )
        )
    if not result:
        raise ImportErrorForUser("未能从该 PayPal CSV 识别可导入交易。")
    return result


def find_sender_email(rows: list[dict[str, str]]) -> str:
    for row in rows:
        normalized = normalize_keys(row)
        sender = value(normalized, "from email address", "absender e-mail-adresse")
        if sender:
            return sender
    return ""


def is_internal_paypal_row(description: str) -> bool:
    lower = description.lower()
    if description in PAYPAL_INTERNAL_TYPES:
        return True
    return any(token in lower for token in PAYPAL_SUPPRESSED_KEYWORDS)
