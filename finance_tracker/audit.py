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
) -> dict[str, Any]:
    items = list(transactions)
    findings: list[AuditFinding] = []
    totals: dict[str, Decimal] = {}
    excluded_totals: dict[str, Decimal] = {}
    exclusion_reasons: dict[str, int] = {}
    warning_indexes: set[int] = set()
    seen_warning_findings: set[tuple[str, int | None, str]] = set()

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
        "totals_by_currency": {key: format_money(value) for key, value in sorted(totals.items())},
        "excluded_totals_by_currency": {key: format_money(value) for key, value in sorted(excluded_totals.items())},
        "excluded_by_reason": dict(sorted(exclusion_reasons.items())),
        "findings": [finding.serializable() for finding in findings],
    }
