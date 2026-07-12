from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from .db import Database
from .domain import ImportPreview, ParsedTransaction
from .importers import ImportErrorForUser, parse_file


class FinanceService:
    def __init__(self, database: Database):
        self.db = database
        self.previews: dict[str, ImportPreview] = {}

    def preview(self, filename: str, content: bytes, source_path: str = "") -> ImportPreview:
        source_type, transactions, warnings = parse_file(filename, content)
        sha256 = hashlib.sha256(content).hexdigest()
        preview = ImportPreview(uuid.uuid4().hex, filename, source_type, sha256, transactions, warnings, self.db.source_exists(sha256))
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
        return {"previews": previews, "errors": errors, "blockers": blockers,
                "can_confirm": not blockers, "total_files": len(files), "baseline": baseline}

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
            return {"available": True, "different": any(x["expected"] != x["actual"] for x in differences.values()), "differences": differences,
                    "note": "基准仅用于本地核对，不参与运行时导入。"}
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
        rules = self.db.active_rules()
        categories = {row["id"]: row for row in self.db.category_rows()}
        prepared_imports = []
        for item, preview in zip(items, selected):
            prepared = [self._prepare(tx, preview.source_type, rules, categories) for tx in preview.transactions]
            self._mark_refunds(prepared)
            prepared_imports.append(({
                "path": str(item.get("source_path", "")), "filename": preview.filename,
                "source_type": preview.source_type, "sha256": preview.file_hash,
            }, prepared))
        written = self.db.write_import_batch(prepared_imports)
        results = []
        for item, result in zip(items, written):
            self.previews.pop(str(item["token"]), None)
            results.append({"token": item["token"], "ok": True, **result})
        if any(not result["duplicate_source"] for result in written):
            self.db.reconcile_paypal()
        return {"results": results}

    def confirm(self, token: str, source_path: str = "") -> dict:
        preview = self.previews.pop(token, None)
        if not preview:
            raise ValueError("导入预览已失效，请重新选择文件。")
        rules = self.db.active_rules()
        category_lookup = {row["id"]: row for row in self.db.category_rows()}
        prepared = [self._prepare(item, preview.source_type, rules, category_lookup) for item in preview.transactions]
        self._mark_refunds(prepared)
        result = self.db.write_import({"path": source_path, "filename": preview.filename, "source_type": preview.source_type, "sha256": preview.file_hash}, prepared)
        if not result["duplicate_source"]:
            result["paypal_matching"] = self.db.reconcile_paypal()
        return result

    def _prepare(self, item: ParsedTransaction, source_type: str, rules, categories) -> dict:
        merchant = item.merchant.upper()
        category_id = None
        reason = "uncategorized"
        excluded_reason = ""
        if item.transaction_kind == "investment":
            category_id = self._category_id(categories, "投资", "现金流", "入金")
            reason, excluded_reason = "structured_source", "investment"
        else:
            for rule in rules:
                if re.search(rule["pattern"], f"{item.merchant} {item.description}", re.I):
                    category_id, reason = rule["category_id"], "merchant_rule"
                    break
        if item.is_internal_transfer:
            excluded_reason = "internal_transfer"
        if item.is_failed_transaction:
            excluded_reason = "failed_transaction"
        if category_id is None:
            if item.amount >= 0:
                category_id = self._category_id(categories, "收入", "其他收入", "其他收入")
            else:
                category_id = self._category_id(categories, "可变支出", "其他", "其他")
        if source_type == "deutsche_bank_pdf":
            # Monthly statements overlap with earlier transaction exports. Merchant text
            # varies by layout, while date/amount/account identify the same bank entry.
            fingerprint_text = "|".join((source_type, item.booking_date.isoformat(), str(item.amount), item.currency, item.account))
        else:
            fingerprint_text = "|".join((source_type, item.external_id, item.booking_date.isoformat(), str(item.amount), item.currency, item.merchant, item.account))
        return {
            "booking_date": item.booking_date.isoformat(), "amount_cents": int(item.amount * 100), "currency": item.currency.upper(),
            "merchant": item.merchant, "description": item.description, "account": item.account, "external_id": item.external_id,
            "transaction_kind": item.transaction_kind, "raw": item.raw, "fingerprint": hashlib.sha256(fingerprint_text.encode()).hexdigest(),
            "value_date": (item.value_date or item.booking_date).isoformat(), "transaction_type": item.transaction_type,
            "source_format": item.source_format or source_type, "is_internal_transfer": int(item.is_internal_transfer),
            "is_failed_transaction": int(item.is_failed_transaction),
            "category_id": category_id, "category_reason": reason, "excluded_reason": excluded_reason,
            "unsupported_currency": int(item.currency.upper() != "EUR"),
        }

    @staticmethod
    def _category_id(categories, level1: str, level2: str, level3: str) -> int:
        for category_id, row in categories.items():
            if (row["level1"], row["level2"], row["level3"]) == (level1, level2, level3):
                return category_id
        raise RuntimeError("分类种子数据缺失")

    @staticmethod
    def _mark_refunds(items: list[dict]) -> None:
        unmatched = set(range(len(items)))
        for i, left in enumerate(items):
            if i not in unmatched:
                continue
            for j in list(unmatched):
                if j <= i:
                    continue
                right = items[j]
                if left["amount_cents"] + right["amount_cents"] != 0:
                    continue
                if abs((date_from_iso(left["booking_date"]) - date_from_iso(right["booking_date"])).days) > 3:
                    continue
                if token_overlap(left["merchant"], right["merchant"]) or "refund" in f"{left['description']} {right['description']}".lower():
                    left["excluded_reason"] = right["excluded_reason"] = "matched_refund_pair"
                    unmatched.discard(i); unmatched.discard(j)
                    break

    def report(self, filters: dict | None = None) -> dict:
        rows = self.db.transaction_rows(include_excluded=False, filters=filters)
        income = sum(row["amount_cents"] for row in rows if row["amount_cents"] > 0)
        expense = sum(row["amount_cents"] for row in rows if row["amount_cents"] < 0)
        monthly = defaultdict(lambda: [0, 0])
        categories = defaultdict(int)
        for row in rows:
            monthly[row["booking_date"][:7]][0 if row["amount_cents"] > 0 else 1] += row["amount_cents"]
            if row["amount_cents"] < 0:
                categories[row["level2"] or "待分类"] += -row["amount_cents"]
        return {"income": income, "expense": expense, "net": income + expense, "count": len(rows),
                "monthly": [{"month": key, "income": value[0], "expense": -value[1]} for key, value in sorted(monthly.items())],
                "categories": [{"name": key, "amount": value} for key, value in sorted(categories.items(), key=lambda pair: pair[1], reverse=True)]}


def date_from_iso(raw: str):
    from datetime import date
    return date.fromisoformat(raw)


def token_overlap(left: str, right: str) -> bool:
    a = {part for part in re.findall(r"\w+", left.lower()) if len(part) > 3}
    b = {part for part in re.findall(r"\w+", right.lower()) if len(part) > 3}
    return bool(a & b)
