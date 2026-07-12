from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from pathlib import Path

from .config import FinanceTrackerConfig, load_config
from .db import Database
from .domain import ImportPreview, ParsedTransaction
from .importers import ImportErrorForUser, parse_file
from .reconciliation import fingerprint_for_transaction, mark_internal_transfers, mark_refund_pairs


class FinanceService:
    def __init__(self, database: Database, config: FinanceTrackerConfig | None = None):
        self.db = database
        self.config = config or load_config()
        self.previews: dict[str, ImportPreview] = {}

    def preview(self, filename: str, content: bytes, source_path: str = "") -> ImportPreview:
        source_type, transactions, warnings = parse_file(filename, content, self.config)
        sha256 = hashlib.sha256(content).hexdigest()
        preview = ImportPreview(
            uuid.uuid4().hex,
            filename,
            source_type,
            sha256,
            transactions,
            warnings,
            self.db.source_exists(sha256),
        )
        self.previews[preview.token] = preview
        return preview

    def preview_many(self, files: list[dict]) -> dict:
        previews = []
        errors = []
        for upload in files:
            try:
                previews.append(self.preview(upload["filename"], upload["content"]).summary())
            except (ValueError, ImportErrorForUser) as error:
                errors.append({"filename": upload["filename"], "error": str(error)})
        blockers = list(errors)
        for item in previews:
            if item["total"] == 0:
                blockers.append({"filename": item["filename"], "error": "未识别到交易"})
            if item["unsupported_currency"]:
                blockers.append({"filename": item["filename"], "error": "包含不支持的非欧元记录"})
        baseline = self._baseline_difference(previews)
        return {"previews": previews, "errors": errors, "blockers": blockers, "can_confirm": not blockers, "total_files": len(files), "baseline": baseline}

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
            return {"available": True, "different": any(x["expected"] != x["actual"] for x in differences.values()), "differences": differences}
        except (OSError, ValueError, TypeError):
            return {"available": False, "different": False}

    def confirm_many(self, items: list[dict]) -> dict:
        if not items:
            raise ValueError("没有可确认的导入文件。")
        selected = [self.previews.get(str(item.get("token", ""))) for item in items]
        if any(preview is None for preview in selected):
            raise ValueError("批量导入预览已失效，请重新选择全部文件。")
        if any(any(tx.currency.upper() != "EUR" for tx in preview.transactions) for preview in selected):
            raise ValueError("批量导入包含非欧元记录，不能统一确认。")
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
        return result

    def _prepare(self, item: ParsedTransaction, source_type: str) -> dict:
        category_id, category_status, category_reason = self._default_category_for(item)
        excluded_reason = ""
        if item.transaction_kind == "investment":
            excluded_reason = "investment"
        if item.is_internal_transfer:
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
            "transaction_kind": item.transaction_kind,
            "transaction_type": item.transaction_type,
            "source_format": item.source_format or source_type,
            "source_record_index": item.source_record_index,
            "source_record_key": item.source_record_key or f"{source_type}:{item.source_record_index}",
            "is_internal_transfer": int(item.is_internal_transfer),
            "is_failed_transaction": int(item.is_failed_transaction),
            "raw": item.raw,
            "category_id": category_id,
            "category_status": category_status,
            "category_reason": category_reason,
            "excluded_reason": excluded_reason,
            "unsupported_currency": int(item.currency.upper() != "EUR"),
        }
        data["fingerprint"] = fingerprint_for_transaction(source_type, data)
        return data

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
