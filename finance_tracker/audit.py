from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable



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
