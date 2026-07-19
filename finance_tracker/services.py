from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .config import FinanceTrackerConfig, load_config
from .audit import AuditTransaction, build_audit
from .db import Database
from .domain import ImportPreview, ParsedTransaction
from .importers import ImportErrorForUser, parse_file
from .merchant_coverage import build_merchant_coverage, load_merchant_baseline
from .merchant_rules import MerchantResolver, MerchantRule
from .statement_directory import StatementDirectoryScanner
from .reconciliation import fingerprint_for_transaction, mark_internal_transfers, mark_refund_pairs


class FinanceService:
    def __init__(self, database: Database, config: FinanceTrackerConfig | None = None):
        self.db = database
        self.config = config or load_config()
        self.previews: dict[str, ImportPreview] = {}

    def merchant_review_impact(self, merchant: str, direction: str) -> dict:
        return self.db.merchant_review_impact(merchant, direction)

    def apply_merchant_review_rule(self, merchant: str, direction: str, category_id: int) -> dict:
        return self.db.apply_merchant_review_rule(merchant, direction, category_id)

    def skip_merchant_review_group(self, merchant: str, direction: str) -> None:
        self.db.skip_merchant_review_group(merchant, direction)

    def preview(self, filename: str, content: bytes, source_path: str = "", account_override: str = "") -> ImportPreview:
        source_type, transactions, warnings = parse_file(filename, content, self.config)
        if account_override in {"ME", "WIFE"}:
            for transaction in transactions:
                transaction.account = account_override
        sha256 = hashlib.sha256(content).hexdigest()
        aggregated_warnings = list(warnings)
        for item in transactions:
            aggregated_warnings.extend(item.warnings)
        preview = ImportPreview(
            uuid.uuid4().hex,
            filename,
            source_type,
            sha256,
            transactions,
            aggregated_warnings,
            self.db.source_exists(sha256),
            parser_warnings=list(warnings),
        )
        self.previews[preview.token] = preview
        return preview

    def preview_many(self, files: list[dict]) -> dict:
        previews: list[dict] = []
        preview_objects: list[ImportPreview] = []
        source_files: list[dict] = []
        transactions: list[dict] = []
        errors: list[dict] = []
        for upload_index, upload in enumerate(files):
            file_hash = hashlib.sha256(upload["content"]).hexdigest()
            source_files.append({
                "upload_index": upload_index,
                "filename": upload["filename"],
                "source_type": "",
                "file_hash": file_hash,
                "duplicate_source": self.db.source_exists(file_hash),
            })
            try:
                account_override = str(upload.get("account_override", ""))
                if account_override:
                    preview = self.preview(upload["filename"], upload["content"], str(upload.get("source_path", "")), account_override)
                else:
                    preview = self.preview(upload["filename"], upload["content"], str(upload.get("source_path", "")))
                source_files[-1].update({
                    "source_type": preview.source_type,
                    "file_hash": preview.file_hash,
                    "duplicate_source": preview.duplicate_source,
                })
                preview_objects.append(preview)
                previews.append(preview.summary())
                transactions.extend(self._preview_rows(preview))
            except (ValueError, ImportErrorForUser) as error:
                errors.append({"filename": upload["filename"], "error": str(error)})
        blockers = list(errors)
        empty_files: list[str] = []
        for item in previews:
            if item["total"] == 0:
                blockers.append({"filename": item["filename"], "error": "未识别到交易"})
                empty_files.append(item["filename"])
            if item["unsupported_currency"]:
                blockers.append({"filename": item["filename"], "error": "包含不支持的非 EUR 记录"})
        baseline = self._baseline_difference(previews)
        audit_transactions = []
        global_index = 0
        for preview in preview_objects:
            for transaction in preview.transactions:
                prepared = self._prepare(transaction, preview.source_type)
                audit_transactions.append(AuditTransaction(
                    global_index, transaction.currency, transaction.amount,
                    list(transaction.warnings), prepared["excluded_reason"],
                    preview.source_type, preview.filename, transaction.booking_date.isoformat(),
                    transaction.merchant_normalized, transaction.description_raw,
                    transaction.external_id, transaction.currency.upper() != "EUR",
                ))
                global_index += 1
        parser_warnings = [warning for preview in preview_objects for warning in preview.parser_warnings]
        audit = build_audit(len(files), audit_transactions, errors, empty_files, parser_warnings, source_files)
        for finding in audit["findings"]:
            if finding["code"] == "DUPLICATE_SOURCE_FILE":
                blockers.append({"filename": ", ".join(finding["details"]["filenames"]), "error": "批次中存在重复源文件"})
            elif finding["code"] == "DUPLICATE_EXTERNAL_ID":
                blockers.append({"filename": "", "error": "批次内存在重复 external ID"})
        return {
            "previews": previews,
            "transactions": transactions,
            "stats": self._preview_stats(transactions),
            "errors": errors,
            "blockers": blockers,
            "can_confirm": audit["can_confirm"],
            "total_files": len(files),
            "baseline": baseline,
            "audit": audit,
        }

    def scan_statement_directory(self, root: Path) -> list[dict]:
        return [
            {"relative_path": item.relative_path, "account": item.account, "status": item.status}
            for item in StatementDirectoryScanner(root, self.db.source_exists).scan()
        ]

    def preview_scanned_files(self, relative_paths: list[str], root: Path) -> dict:
        root = root.resolve()
        available = {item.relative_path: item for item in StatementDirectoryScanner(root, self.db.source_exists).scan()}
        files = []
        for relative_path in relative_paths:
            item = available.get(relative_path)
            if item is None:
                raise ValueError("所选账单不在扫描目录中。")
            if item.status == "already_imported":
                raise ValueError("所选账单已经导入。")
            if item.status != "ready":
                raise ValueError("所选 CSV 尚未确认账户归属。")
            path = (root / item.relative_path).resolve()
            if not path.is_relative_to(root):
                raise ValueError("所选账单路径无效。")
            files.append({"filename": path.name, "content": path.read_bytes(), "source_path": str(path), "account_override": item.account})
        result = self.preview_many(files)
        result["source_paths"] = {preview["token"]: files[index]["source_path"] for index, preview in enumerate(result["previews"])}
        return result

    @staticmethod
    def _baseline_difference(previews: list[dict]) -> dict:
        path = Path(__file__).resolve().parent.parent / "bank_transactions.json"
        if not path.is_file():
            return {"available": False, "different": False}
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            rows = cached if isinstance(cached, list) else cached.get("transactions", [])
            total = sum(preview["total"] for preview in previews)
            dates_from = [preview["date_from"] for preview in previews if preview["date_from"]]
            dates_to = [preview["date_to"] for preview in previews if preview["date_to"]]
            expected_dates = [row.get("booking_date", "") for row in rows if row.get("booking_date")]
            expected_income = round(sum(float(row.get("amount", 0)) for row in rows if float(row.get("amount", 0)) > 0) * 100)
            expected_expense = round(sum(float(row.get("amount", 0)) for row in rows if float(row.get("amount", 0)) < 0) * 100)
            differences = {
                "total": {"expected": len(rows), "actual": total},
                "date_from": {"expected": min(expected_dates) if expected_dates else "", "actual": min(dates_from) if dates_from else ""},
                "date_to": {"expected": max(expected_dates) if expected_dates else "", "actual": max(dates_to) if dates_to else ""},
                "income_cents": {"expected": expected_income, "actual": sum(x["income_cents"] for x in previews)},
                "expense_cents": {"expected": expected_expense, "actual": sum(x["expense_cents"] for x in previews)},
            }
            return {"available": True, "different": any(item["expected"] != item["actual"] for item in differences.values()), "differences": differences}
        except (OSError, ValueError, TypeError):
            return {"available": False, "different": False}

    @staticmethod
    def _preview_stats(transactions: list[dict]) -> dict:
        warning_count = sum(1 for item in transactions if item["warnings"])
        internal_transfer_count = sum(1 for item in transactions if item["is_internal_transfer"])
        currency_exchange_count = sum(1 for item in transactions if item.get("transaction_kind") == "currency_exchange")
        failed_transaction_count = sum(1 for item in transactions if item["is_failed_transaction"])
        unsupported_currency_count = sum(1 for item in transactions if item["currency"].upper() != "EUR")
        unknown_merchant_count = sum(1 for item in transactions if not item["merchant_normalized"] or item["merchant_normalized"].lower().startswith("unknown "))
        income_cents = sum(int(Decimal(item["amount"]) * 100) for item in transactions if item["currency"].upper() == "EUR" and Decimal(item["amount"]) > 0)
        expense_cents = sum(int(Decimal(item["amount"]) * 100) for item in transactions if item["currency"].upper() == "EUR" and Decimal(item["amount"]) < 0)
        return {
            "total": len(transactions),
            "warning_count": warning_count,
            "internal_transfer_count": internal_transfer_count,
            "currency_exchange_count": currency_exchange_count,
            "failed_transaction_count": failed_transaction_count,
            "unsupported_currency_count": unsupported_currency_count,
            "unknown_merchant_count": unknown_merchant_count,
            "income_cents": income_cents,
            "expense_cents": expense_cents,
        }

    def confirm_many(self, items: list[dict]) -> dict:
        if not items:
            raise ValueError("没有可确认的导入文件。")
        selected = [self.previews.get(str(item.get("token", ""))) for item in items]
        if any(preview is None for preview in selected):
            raise ValueError("批量导入预览已失效，请重新选择全部文件。")
        if any(any(tx.currency.upper() != "EUR" for tx in preview.transactions) for preview in selected):
            raise ValueError("批量导入包含非 EUR 记录，不能统一确认。")
        prepared_imports = []
        for item, preview in zip(items, selected):
            prepared = [self._prepare(tx, preview.source_type) for tx in preview.transactions]
            mark_internal_transfers(prepared, self.config)
            mark_refund_pairs(prepared)
            prepared_imports.append(({
                "path": str(item.get("source_path", "")),
                "filename": preview.filename,
                "source_type": preview.source_type,
                "sha256": preview.file_hash,
            }, prepared))
        baseline = self._baseline_difference([preview.summary() for preview in selected])
        written = self.db.write_import_batch(prepared_imports, baseline_difference=baseline if baseline.get("available") else None)
        results = []
        for item, result in zip(items, written):
            self.previews.pop(str(item["token"]), None)
            results.append({"token": item["token"], "ok": True, **result})
        if any(not result["duplicate_source"] for result in written):
            self.db.reconcile_paypal()
            self.db.reconcile_refunds()
        return {"results": results, "baseline": baseline}

    def confirm(self, token: str, source_path: str = "") -> dict:
        preview = self.previews.pop(token, None)
        if not preview:
            raise ValueError("导入预览已失效，请重新选择文件。")
        prepared = [self._prepare(item, preview.source_type) for item in preview.transactions]
        mark_internal_transfers(prepared, self.config)
        mark_refund_pairs(prepared)
        result = self.db.write_import({"path": source_path, "filename": preview.filename, "source_type": preview.source_type, "sha256": preview.file_hash}, prepared)
        if not result["duplicate_source"]:
            result["paypal_matching"] = self.db.reconcile_paypal()
            result["refund_matching"] = self.db.reconcile_refunds()
        return result

    def _prepare(self, item: ParsedTransaction, source_type: str) -> dict:
        transaction_kind = item.transaction_kind
        if transaction_kind in ("", "cash"):
            transaction_kind = self._currency_exchange_kind(item, source_type) or transaction_kind
        category_id, category_status, category_reason = self._default_category_for(item)
        excluded_reason = ""
        is_internal_transfer = int(item.is_internal_transfer)
        if transaction_kind == "investment":
            excluded_reason = "investment"
        if transaction_kind == "currency_exchange":
            excluded_reason = "currency_exchange"
            is_internal_transfer = 0
        elif item.is_internal_transfer:
            excluded_reason = "internal_transfer"
        if item.is_failed_transaction:
            excluded_reason = "failed_transaction"
        data = {
            "booking_date": item.booking_date.isoformat(),
            "value_date": (item.value_date or item.booking_date).isoformat(),
            "amount_cents": int(item.amount * 100),
            "currency": item.currency.upper(),
            "merchant_raw": item.merchant_raw,
            "merchant": item.merchant_normalized,
            "description": item.description_raw,
            "account": item.account,
            "external_id": item.external_id,
            "transaction_kind": transaction_kind or item.transaction_kind,
            "transaction_type": item.transaction_type,
            "source_format": item.source_format or source_type,
            "source_record_index": item.source_record_index,
            "source_record_key": item.source_record_key or f"{source_type}:{item.source_record_index}",
            "is_internal_transfer": is_internal_transfer,
            "is_failed_transaction": int(item.is_failed_transaction),
            "raw": item.raw,
            "category_id": category_id,
            "category_status": category_status,
            "category_reason": category_reason,
            "canonical_merchant_id": None,
            "excluded_reason": excluded_reason,
            "unsupported_currency": int(item.currency.upper() != "EUR"),
        }
        resolution = self._merchant_resolver().resolve(
            data["merchant"], data["amount_cents"], data["category_status"], data["transaction_kind"], data["excluded_reason"],
        )
        if resolution.canonical_merchant_id is not None:
            data["canonical_merchant_id"] = resolution.canonical_merchant_id
            data["canonical_merchant"] = resolution.canonical_merchant
            data["category_id"] = resolution.category_id
            data["category_status"] = resolution.category_status
            data["category_reason"] = resolution.category_reason
        data["fingerprint"] = fingerprint_for_transaction(source_type, data)
        return data

    def _preview_rows(self, preview: ImportPreview) -> list[dict]:
        rows = preview.details()
        for row, item in zip(rows, preview.transactions):
            prepared = self._prepare(item, preview.source_type)
            row.update({key: prepared[key] for key in ("canonical_merchant_id", "category_id", "category_status", "category_reason")})
            row["canonical_merchant"] = prepared.get("canonical_merchant", "")
            transaction_kind = item.transaction_kind
            if transaction_kind in ("", "cash"):
                transaction_kind = self._currency_exchange_kind(item, preview.source_type) or transaction_kind
            row["transaction_kind"] = transaction_kind
            if transaction_kind == "currency_exchange":
                row["is_internal_transfer"] = False
        return rows

    def _merchant_resolver(self) -> MerchantResolver:
        return MerchantResolver([
            MerchantRule(
                row["canonical_merchant"], row["pattern"], row["match_kind"], row["direction"], row["category_id"], row["canonical_merchant_id"],
            )
            for row in self.db.active_rules()
        ])

    def import_legacy_baseline_rules(self, path: Path | None = None) -> dict[str, int]:
        baseline_path = path or Path(__file__).resolve().parent.parent / "legacy" / "categories.md"
        return self.db.import_legacy_baseline_rules(load_merchant_baseline(baseline_path))

    def _currency_exchange_kind(self, item: ParsedTransaction, source_type: str) -> str:
        text = "\n".join(
            value for value in (
                item.merchant_raw,
                item.merchant_normalized,
                item.description_raw,
                json.dumps(item.raw, ensure_ascii=False),
            ) if value
        )
        return self.config.currency_exchange_kind_for(source_type, text)

    def _default_category_for(self, item: ParsedTransaction) -> tuple[int, str, str]:
        categories = {row["level3"]: row["id"] for row in self.db.category_rows()}
        if item.transaction_kind == "investment":
            return categories["入金"], "system", "structured_source"
        if item.amount >= 0:
            return categories["待人工分类收入"], "unclassified", "phase_1_default"
        return categories["待人工分类支出"], "unclassified", "phase_1_default"

    def report(self, filters: dict | None = None) -> dict:
        rows = self.db.transaction_rows(include_excluded=False, filters=filters)
        income = sum(row["amount_cents"] for row in rows if row["amount_cents"] > 0)
        expense = sum(row["amount_cents"] for row in rows if row["amount_cents"] < 0)
        monthly = defaultdict(lambda: [0, 0])
        categories = defaultdict(int)
        for row in rows:
            monthly[row["booking_date"][:7]][0 if row["amount_cents"] > 0 else 1] += row["amount_cents"]
            categories[row["level2"] or "待分类"] += abs(row["amount_cents"])
        return {
            "income": income,
            "expense": expense,
            "net": income + expense,
            "count": len(rows),
            "monthly": [{"month": key, "income": value[0], "expense": -value[1]} for key, value in sorted(monthly.items())],
            "categories": [{"name": key, "amount": value} for key, value in sorted(categories.items(), key=lambda pair: pair[1], reverse=True)],
        }

    def merchant_coverage(self) -> dict:
        return build_merchant_coverage(self._coverage_rows(), self._merchant_baseline_rules())

    def historical_merchant_coverage(self, path: Path | None = None) -> dict:
        historical_path = path or Path(__file__).resolve().parent.parent / "bank_transactions.json"
        data = json.loads(historical_path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("transactions", [])
        if not isinstance(rows, list):
            raise ValueError("Historical transaction data must contain a transaction list.")
        return build_merchant_coverage(
            rows,
            self._merchant_baseline_rules(),
            infer_legacy_currency_exchange=True,
            missing_currency="EUR",
        )

    def _coverage_rows(self) -> list[dict]:
        return [dict(row) for row in self.db.transaction_rows()]

    @staticmethod
    def _merchant_baseline_rules():
        path = Path(__file__).resolve().parent.parent / "legacy" / "categories.md"
        return load_merchant_baseline(path)
