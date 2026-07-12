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

PAYPAL_STRICT_INTERNAL_PATTERNS = (
    "reversal of ach deposit",
    "reversal of ach withdrawal transaction",
    "account hold for open authorization",
    "reversal of general account hold",
    "ach-überweisung als zahlungsquelle für ausgleich von kontoguthaben",
    "rückbuchung von ach-gutschrift",
    "rückbuchung allgemeiner einbehaltung",
    "einbehaltung für offene autorisierung",
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
        internal_match, suspicious = classify_paypal_row(description)
        if internal_match:
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
        warnings = [f"Suspicious PayPal internal-like transaction type kept for review: {description}"] if suspicious else []
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
                warnings=warnings,
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


def classify_paypal_row(description: str) -> tuple[bool, bool]:
    normalized = normalize_paypal_description(description)
    if normalized in {normalize_paypal_description(item) for item in PAYPAL_INTERNAL_TYPES}:
        return True, False
    if normalized in PAYPAL_STRICT_INTERNAL_PATTERNS:
        return True, False
    suspicious = any(token in normalized for token in ("authorization", "autorisierung", "hold", "einbehaltung", "deposit", "gutschrift", "withdrawal", "abbuchung"))
    return False, suspicious


def is_internal_paypal_row(description: str) -> bool:
    return classify_paypal_row(description)[0]


def normalize_paypal_description(description: str) -> str:
    lower = description.lower()
    return lower.replace("r眉", "rü").replace("脺", "ü")
