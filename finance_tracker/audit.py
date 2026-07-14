from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .reconciliation.paypal import paypal_bank_date_difference


@dataclass(slots=True)
class AuditFinding:
    code: str
    severity: str
    message: str
    transaction_indexes: list[int] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def serializable(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "transaction_indexes": list(self.transaction_indexes),
            "details": dict(self.details),
        }


@dataclass(slots=True)
class AuditTransaction:
    index: int
    currency: str
    amount: Decimal
    warnings: list[str] = field(default_factory=list)
    excluded_reason: str = ""
    source_type: str = ""
    filename: str = ""
    booking_date: str = ""
    merchant: str = ""
    description: str = ""
    external_id: str = ""
    unsupported_currency: bool = False


def format_money(value: Decimal) -> str:
    """Format exact money with at least two decimals, without float conversion."""
    if value == 0:
        return "0.00"
    whole, separator, fraction = format(value, "f").partition(".")
    if separator:
        fraction = fraction.rstrip("0")
    return f"{whole}.{fraction.ljust(2, '0')}"


def preview_paypal_bank_candidate(paypal: AuditTransaction, bank: AuditTransaction) -> bool:
    if paypal.unsupported_currency or bank.unsupported_currency:
        return False
    if paypal.currency.upper() != bank.currency.upper() or paypal.amount != bank.amount:
        return False
    if paypal_bank_date_difference({"booking_date": paypal.booking_date}, {"booking_date": bank.booking_date}) > 5:
        return False
    return "PAYPAL" in f"{bank.merchant} {bank.description}".upper()


def build_audit(
    source_file_count: int,
    transactions: Iterable[AuditTransaction],
    errors: list[dict[str, str]],
    empty_files: list[str],
    parser_warnings: Iterable[str] = (),
    source_files: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    items = list(transactions)
    findings: list[AuditFinding] = []
    totals: dict[str, Decimal] = {}
    excluded_totals: dict[str, Decimal] = {}
    exclusion_reasons: dict[str, int] = {}
    warning_indexes: set[int] = set()
    seen_warning_findings: set[tuple[str, int | None, str]] = set()
    source_file_items = list(source_files)
    source_groups: dict[str, list[dict[str, Any]]] = {}
    for source in source_file_items:
        source_groups.setdefault(source["file_hash"], []).append(source)
    for file_hash, group in source_groups.items():
        if any(source.get("duplicate_source") for source in group) or len(group) > 1:
            findings.append(AuditFinding(
                "DUPLICATE_SOURCE_FILE", "blocker", "Duplicate source file hash in database or upload batch.",
                details={
                    "file_hash": file_hash,
                    "exists_in_database": any(source.get("duplicate_source") for source in group),
                    "occurrence_count": len(group),
                    "filenames": [source["filename"] for source in group],
                    "source_types": [source.get("source_type", "") for source in group],
                    "upload_indexes": [source["upload_index"] for source in group],
                },
            ))

    external_groups: dict[tuple[str, str], list[AuditTransaction]] = {}
    for item in items:
        external_id = item.external_id.strip()
        if external_id:
            external_groups.setdefault((item.source_type, external_id), []).append(item)
    for (source_type, external_id), group in sorted(external_groups.items(), key=lambda pair: (min(item.index for item in pair[1]), pair[0])):
        if len(group) > 1:
            findings.append(AuditFinding(
                "DUPLICATE_EXTERNAL_ID", "blocker", "Duplicate external transaction ID in upload batch.",
                sorted(item.index for item in group),
                {"source_type": source_type, "external_id": external_id, "occurrence_count": len(group)},
            ))

    for item in items:
        currency = item.currency.upper()
        totals[currency] = totals.get(currency, Decimal("0")) + item.amount
        if item.excluded_reason:
            exclusion_reasons[item.excluded_reason] = exclusion_reasons.get(item.excluded_reason, 0) + 1
            excluded_totals[currency] = excluded_totals.get(currency, Decimal("0")) + item.amount
        if item.warnings:
            warning_indexes.add(item.index)
            for warning in item.warnings:
                key = ("TRANSACTION_WARNING", item.index, warning)
                if key not in seen_warning_findings:
                    findings.append(AuditFinding(
                        "TRANSACTION_WARNING", "warning", warning,
                        [item.index], {"warning": warning},
                    ))
                    seen_warning_findings.add(key)
        if currency != "EUR":
            findings.append(AuditFinding(
                "UNSUPPORTED_CURRENCY", "blocker",
                f"Transaction currency {currency} is not supported; only EUR can be confirmed.",
                [item.index], {"currency": currency},
            ))

    paypal_items = [item for item in items if item.source_type == "paypal_csv"]
    bank_items = [item for item in items if item.source_type == "deutsche_bank_pdf"]
    relationships: list[tuple[AuditTransaction, AuditTransaction]] = [
        (paypal, bank) for paypal in paypal_items for bank in bank_items
        if preview_paypal_bank_candidate(paypal, bank)
    ]
    paypal_candidates: dict[int, list[int]] = {}
    bank_candidates: dict[int, list[int]] = {}
    for paypal, bank in relationships:
        paypal_candidates.setdefault(paypal.index, []).append(bank.index)
        bank_candidates.setdefault(bank.index, []).append(paypal.index)
    ambiguous_edges = [edge for edge in relationships if len(paypal_candidates[edge[0].index]) > 1 or len(bank_candidates[edge[1].index]) > 1]
    visited: set[int] = set()
    for paypal, bank in sorted(ambiguous_edges, key=lambda edge: (edge[0].index, edge[1].index)):
        if paypal.index in visited:
            continue
        component_paypal: set[int] = set()
        component_bank: set[int] = set()
        frontier = [paypal.index]
        while frontier:
            current_paypal = frontier.pop()
            if current_paypal in component_paypal:
                continue
            component_paypal.add(current_paypal)
            for bank_index in paypal_candidates.get(current_paypal, []):
                component_bank.add(bank_index)
                for paypal_index in bank_candidates.get(bank_index, []):
                    if paypal_index not in component_paypal:
                        frontier.append(paypal_index)
        visited.update(component_paypal)
        indexes = sorted(component_paypal | component_bank)
        findings.append(AuditFinding(
            "PAYPAL_BANK_AMBIGUOUS", "warning", "PayPal-to-bank overlap has multiple eligible candidates.", indexes,
            {"paypal_indexes": sorted(component_paypal), "bank_indexes": sorted(component_bank),
             "candidate_relationships": [{"paypal_index": left.index, "bank_index": right.index} for left, right in sorted(ambiguous_edges, key=lambda edge: (edge[0].index, edge[1].index)) if left.index in component_paypal and right.index in component_bank],
             "reason": "mutual uniqueness is not satisfied"},
        ))
    for paypal, bank in sorted(relationships, key=lambda edge: (edge[0].index, edge[1].index)):
        if len(paypal_candidates[paypal.index]) == 1 and len(bank_candidates[bank.index]) == 1:
            days = paypal_bank_date_difference(
                {"booking_date": paypal.booking_date}, {"booking_date": bank.booking_date}
            )
            findings.append(AuditFinding(
                "PAYPAL_BANK_MATCH", "info", "PayPal and Deutsche Bank transactions are a mutually unique overlap.",
                [paypal.index, bank.index],
                {"paypal_index": paypal.index, "bank_index": bank.index, "canonical_amount": format_money(paypal.amount),
                 "currency": paypal.currency.upper(), "paypal_booking_date": paypal.booking_date,
                 "bank_booking_date": bank.booking_date, "date_difference_days": days,
                 "reason": "exact amount, currency, bank PayPal text, and mutually unique five-day candidate"},
            ))

    for warning in parser_warnings:
        key = ("PARSER_WARNING", None, warning)
        if key not in seen_warning_findings:
            findings.append(AuditFinding("PARSER_WARNING", "warning", warning, details={"warning": warning}))
            seen_warning_findings.add(key)

    for error in errors:
        findings.append(AuditFinding("IMPORT_ERROR", "blocker", error["error"], details={"filename": error["filename"]}))
    for filename in empty_files:
        findings.append(AuditFinding("NO_TRANSACTIONS", "blocker", "No transactions were recognized.", details={"filename": filename}))

    blocker_count = sum(finding.severity == "blocker" for finding in findings)
    warning_count = sum(finding.severity == "warning" for finding in findings)
    status = "blocked" if blocker_count else "warning" if warning_count else "pass"
    return {
        "status": status,
        "can_confirm": status != "blocked",
        "source_file_count": source_file_count,
        "parsed_transaction_count": len(items),
        "excluded_transaction_count": sum(exclusion_reasons.values()),
        "warning_transaction_count": len(warning_indexes),
        "blocking_finding_count": blocker_count,
        "warning_finding_count": warning_count,
        "info_finding_count": sum(finding.severity == "info" for finding in findings),
        "totals_by_currency": {key: format_money(value) for key, value in sorted(totals.items())},
        "excluded_totals_by_currency": {key: format_money(value) for key, value in sorted(excluded_totals.items())},
        "excluded_by_reason": dict(sorted(exclusion_reasons.items())),
        "findings": [finding.serializable() for finding in findings],
    }
