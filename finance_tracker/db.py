from __future__ import annotations

import json
import hashlib
import sqlite3
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .categories import DEFAULT_CATEGORIES
from .reconciliation import reconcile_paypal_rows


SCHEMA_VERSION = 5


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        if self._needs_transaction_columns():
            self._backup_before_schema_change()
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_files (
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL, filename TEXT NOT NULL,
                    source_type TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, imported_at TEXT NOT NULL,
                    parser_version TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY, source_file_id INTEGER NOT NULL REFERENCES source_files(id),
                    imported_at TEXT NOT NULL, accepted_count INTEGER NOT NULL, rejected_count INTEGER NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY, level1 TEXT NOT NULL, level2 TEXT NOT NULL, level3 TEXT NOT NULL,
                    bucket TEXT NOT NULL CHECK(bucket IN ('income','expense','excluded','investment')),
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(level1, level2, level3)
                );
                CREATE TABLE IF NOT EXISTS canonical_merchants (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS merchant_aliases (
                    id INTEGER PRIMARY KEY, canonical_merchant_id INTEGER NOT NULL REFERENCES canonical_merchants(id),
                    pattern TEXT NOT NULL, match_kind TEXT NOT NULL CHECK(match_kind IN ('exact','contains')),
                    source TEXT NOT NULL, UNIQUE(canonical_merchant_id, pattern, match_kind, source)
                );
                CREATE TABLE IF NOT EXISTS merchant_category_rules (
                    id INTEGER PRIMARY KEY, canonical_merchant_id INTEGER NOT NULL REFERENCES canonical_merchants(id),
                    direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
                    category_id INTEGER NOT NULL REFERENCES categories(id), source TEXT NOT NULL,
                    UNIQUE(canonical_merchant_id, direction, category_id, source)
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY, source_file_id INTEGER NOT NULL REFERENCES source_files(id),
                    booking_date TEXT NOT NULL, value_date TEXT NOT NULL DEFAULT '',
                    amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
                    merchant_raw TEXT NOT NULL, merchant TEXT NOT NULL, description TEXT NOT NULL,
                    account TEXT NOT NULL, external_id TEXT NOT NULL DEFAULT '',
                    transaction_kind TEXT NOT NULL DEFAULT 'cash', transaction_type TEXT NOT NULL DEFAULT '',
                    source_format TEXT NOT NULL DEFAULT '', source_record_index INTEGER NOT NULL DEFAULT 0,
                    source_record_key TEXT NOT NULL DEFAULT '', is_internal_transfer INTEGER NOT NULL DEFAULT 0,
                    is_failed_transaction INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE, category_id INTEGER REFERENCES categories(id),
                    category_status TEXT NOT NULL DEFAULT 'unclassified',
                    category_reason TEXT NOT NULL DEFAULT 'unclassified',
                    canonical_merchant_id INTEGER REFERENCES canonical_merchants(id),
                    excluded_reason TEXT NOT NULL DEFAULT '', unsupported_currency INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliations (
                    id INTEGER PRIMARY KEY, left_transaction_id INTEGER NOT NULL REFERENCES transactions(id),
                    right_transaction_id INTEGER NOT NULL REFERENCES transactions(id), kind TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'suggested',
                    UNIQUE(left_transaction_id, right_transaction_id, kind)
                );
                CREATE TABLE IF NOT EXISTS import_runs (
                    id INTEGER PRIMARY KEY, imported_at TEXT NOT NULL, file_count INTEGER NOT NULL,
                    baseline_difference_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY, transaction_id INTEGER REFERENCES transactions(id), action TEXT NOT NULL,
                    before_json TEXT NOT NULL DEFAULT '{}', after_json TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS merchant_review_progress (
                    merchant TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
                    status TEXT NOT NULL CHECK(status IN ('skipped','completed')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(merchant, direction)
                );
                CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(booking_date);
                CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(source_file_id);
                CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category_id);
                """
            )
            missing_columns = {
                "category_status": "TEXT NOT NULL DEFAULT 'unclassified'",
                "canonical_merchant_id": "INTEGER REFERENCES canonical_merchants(id)",
            }
            for column, definition in missing_columns.items():
                if not self._has_column(con, "transactions", column):
                    con.execute(f"ALTER TABLE transactions ADD COLUMN {column} {definition}")
            con.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)", (SCHEMA_VERSION, now()))
            for level1, level2, level3, bucket in DEFAULT_CATEGORIES:
                con.execute("INSERT OR IGNORE INTO categories(level1,level2,level3,bucket) VALUES(?,?,?,?)", (level1, level2, level3, bucket))

    def source_exists(self, sha256: str) -> bool:
        with self.connect() as con:
            return con.execute("SELECT 1 FROM source_files WHERE sha256=?", (sha256,)).fetchone() is not None

    def category_rows(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM categories WHERE active=1 ORDER BY level1, level2, level3").fetchall()

    def add_category(self, level1: str, level2: str, level3: str, bucket: str) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO categories(level1,level2,level3,bucket) VALUES(?,?,?,?)", (level1.strip(), level2.strip(), level3.strip(), bucket))

    def active_rules(self) -> list[sqlite3.Row]:
        return self.rule_rows()

    def rule_rows(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """SELECT r.id AS rule_id, m.id AS canonical_merchant_id, m.name AS canonical_merchant,
                a.id AS alias_id, a.pattern, a.match_kind, r.direction, r.category_id,
                r.source AS rule_source, a.source AS alias_source
                FROM merchant_category_rules r
                JOIN canonical_merchants m ON m.id=r.canonical_merchant_id
                JOIN merchant_aliases a ON a.canonical_merchant_id=m.id
                ORDER BY r.id, a.id"""
            ).fetchall()

    def upsert_canonical_merchant(self, name: str, source: str) -> int:
        with self.connect() as con:
            return self._upsert_canonical_merchant(con, name, source)

    def upsert_merchant_alias(self, canonical_merchant_id: int, pattern: str, match_kind: str, source: str) -> int:
        if match_kind not in ("exact", "contains"):
            raise ValueError("商户别名匹配方式必须是 exact 或 contains。")
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO merchant_aliases(canonical_merchant_id,pattern,match_kind,source) VALUES(?,?,?,?)",
                (canonical_merchant_id, pattern.strip(), match_kind, source),
            )
            return con.execute(
                "SELECT id FROM merchant_aliases WHERE canonical_merchant_id=? AND pattern=? AND match_kind=? AND source=?",
                (canonical_merchant_id, pattern.strip(), match_kind, source),
            ).fetchone()["id"]

    def upsert_merchant_category_rule(self, canonical_merchant_id: int, direction: str, category_id: int, source: str) -> int:
        if direction not in ("income", "expense"):
            raise ValueError("商户分类规则方向必须是 income 或 expense。")
        with self.connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO merchant_category_rules(canonical_merchant_id,direction,category_id,source) VALUES(?,?,?,?)",
                (canonical_merchant_id, direction, category_id, source),
            )
            return con.execute(
                "SELECT id FROM merchant_category_rules WHERE canonical_merchant_id=? AND direction=? AND category_id=? AND source=?",
                (canonical_merchant_id, direction, category_id, source),
            ).fetchone()["id"]

    def import_legacy_baseline_rules(self, rules: list[object]) -> dict[str, int]:
        from .merchant_rules import MerchantResolver, MerchantRule

        rules_created = transactions_updated = conflicts = 0
        with self.connect() as con:
            for baseline in rules:
                direction = baseline.direction
                level1 = "收入" if direction == "income" else "支出"
                con.execute(
                    "INSERT OR IGNORE INTO categories(level1,level2,level3,bucket) VALUES(?,?,?,?)",
                    (level1, baseline.category, baseline.category, direction),
                )
                category_id = con.execute(
                    "SELECT id FROM categories WHERE level1=? AND level2=? AND level3=?",
                    (level1, baseline.category, baseline.category),
                ).fetchone()["id"]
                merchant_id = self._upsert_canonical_merchant(con, baseline.keyword, "legacy_baseline")
                con.execute(
                    "INSERT OR IGNORE INTO merchant_aliases(canonical_merchant_id,pattern,match_kind,source) VALUES(?,?,?,?)",
                    (merchant_id, baseline.keyword, "contains", "legacy_baseline"),
                )
                result = con.execute(
                    "INSERT OR IGNORE INTO merchant_category_rules(canonical_merchant_id,direction,category_id,source) VALUES(?,?,?,?)",
                    (merchant_id, direction, category_id, "legacy_baseline"),
                )
                rules_created += result.rowcount

            rule_rows = con.execute(
                """SELECT m.id AS canonical_merchant_id, m.name AS canonical_merchant, a.pattern, a.match_kind,
                r.direction, r.category_id FROM merchant_category_rules r
                JOIN canonical_merchants m ON m.id=r.canonical_merchant_id
                JOIN merchant_aliases a ON a.canonical_merchant_id=m.id"""
            ).fetchall()
            resolver = MerchantResolver([
                MerchantRule(row["canonical_merchant"], row["pattern"], row["match_kind"], row["direction"], row["category_id"], row["canonical_merchant_id"])
                for row in rule_rows
            ])
            transactions = con.execute(
                """SELECT * FROM transactions WHERE category_status='unclassified' AND transaction_kind='cash'
                AND excluded_reason='' AND amount_cents != 0"""
            ).fetchall()
            for transaction in transactions:
                resolution = resolver.resolve(transaction["merchant"], transaction["amount_cents"], transaction["category_status"], transaction["transaction_kind"], transaction["excluded_reason"])
                if resolution.category_reason.startswith("rule_conflict_"):
                    conflicts += 1
                    continue
                if resolution.canonical_merchant_id is None:
                    continue
                before = {key: transaction[key] for key in ("canonical_merchant_id", "category_id", "category_status", "category_reason")}
                after = {
                    "canonical_merchant_id": resolution.canonical_merchant_id,
                    "category_id": resolution.category_id,
                    "category_status": resolution.category_status,
                    "category_reason": "legacy_baseline_rule",
                }
                con.execute(
                    "UPDATE transactions SET canonical_merchant_id=?, category_id=?, category_status=?, category_reason=? WHERE id=?",
                    (resolution.canonical_merchant_id, resolution.category_id, resolution.category_status, "legacy_baseline_rule", transaction["id"]),
                )
                con.execute(
                    "INSERT INTO audit_log(transaction_id,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?)",
                    (transaction["id"], "merchant_rule_backfill", json.dumps(before), json.dumps(after), "legacy_baseline", now()),
                )
                transactions_updated += 1
        return {"rules_created": rules_created, "transactions_updated": transactions_updated, "conflicts": conflicts}

    def audit_count(self, action: str) -> int:
        with self.connect() as con:
            return con.execute("SELECT COUNT(*) FROM audit_log WHERE action=?", (action,)).fetchone()[0]

    def _upsert_canonical_merchant(self, con: sqlite3.Connection, name: str, source: str) -> int:
        con.execute("INSERT OR IGNORE INTO canonical_merchants(name,source) VALUES(?,?)", (name.strip(), source))
        return con.execute("SELECT id FROM canonical_merchants WHERE name=?", (name.strip(),)).fetchone()["id"]

    def _needs_transaction_columns(self) -> bool:
        if not self.path.is_file() or self.path.stat().st_size == 0:
            return False
        con = sqlite3.connect(self.path)
        try:
            return self._has_column(con, "transactions", "id") and any(
                not self._has_column(con, "transactions", column)
                for column in ("category_status", "canonical_merchant_id")
            )
        finally:
            con.close()

    def _backup_before_schema_change(self) -> Path:
        backups = self.path.parent.parent / "exports" / "backups" / "schema"
        backups.mkdir(parents=True, exist_ok=True)
        target = backups / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-canonical-merchant.sqlite3"
        shutil.copy2(self.path, target)
        if self._sha256(self.path) != self._sha256(target):
            target.unlink(missing_ok=True)
            raise RuntimeError("数据库结构迁移备份校验失败。")
        return target

    @staticmethod
    def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
        return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def add_rule(self, pattern: str, category_id: int, priority: int = 100) -> None:
        raise ValueError("第一阶段不启用自动分类规则。")

    def write_import(self, source: dict, transactions: list[dict]) -> dict:
        return self.write_import_batch([(source, transactions)])[0]

    def write_import_batch(self, imports: list[tuple[dict, list[dict]]], baseline_difference: dict | None = None) -> list[dict]:
        results: list[dict] = []
        with self.connect() as con:
            run = con.execute(
                "INSERT INTO import_runs(imported_at,file_count,baseline_difference_json) VALUES(?,?,?)",
                (now(), len(imports), json.dumps(baseline_difference or {}, ensure_ascii=False)),
            )
            run_id = run.lastrowid
            for source, transactions in imports:
                existing = con.execute("SELECT id FROM source_files WHERE sha256=?", (source["sha256"],)).fetchone()
                if existing:
                    results.append({"duplicate_source": True, "inserted": 0, "rejected": 0})
                    continue
                cur = con.execute(
                    """INSERT INTO source_files(path,filename,source_type,sha256,imported_at,parser_version,record_count)
                    VALUES(?,?,?,?,?,?,?)""",
                    (source["path"], source["filename"], source["source_type"], source["sha256"], now(), "0.3.0", len(transactions)),
                )
                source_id = cur.lastrowid
                inserted = rejected = 0
                for item in transactions:
                    rejected += int(bool(item["unsupported_currency"]))
                    con.execute(
                        """INSERT INTO transactions(
                        source_file_id,booking_date,value_date,amount_cents,currency,merchant_raw,merchant,description,account,external_id,
                        transaction_kind,transaction_type,source_format,source_record_index,source_record_key,is_internal_transfer,
                        is_failed_transaction,raw_json,fingerprint,category_id,category_status,category_reason,canonical_merchant_id,excluded_reason,unsupported_currency,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            source_id, item["booking_date"], item["value_date"], item["amount_cents"], item["currency"],
                            item["merchant_raw"], item["merchant"], item["description"], item["account"], item["external_id"],
                            item["transaction_kind"], item["transaction_type"], item["source_format"], item["source_record_index"],
                            item["source_record_key"], item["is_internal_transfer"], item["is_failed_transaction"],
                            json.dumps(item["raw"], ensure_ascii=False), item["fingerprint"], item["category_id"],
                            item["category_status"], item["category_reason"], item.get("canonical_merchant_id"), item["excluded_reason"], item["unsupported_currency"], now(),
                        ),
                    )
                    inserted += 1
                con.execute(
                    "INSERT INTO import_batches(source_file_id,imported_at,accepted_count,rejected_count,notes) VALUES(?,?,?,?,?)",
                    (source_id, now(), inserted, rejected, f"unified_batch:{run_id}"),
                )
                results.append({"duplicate_source": False, "inserted": inserted, "rejected": rejected})
        return results

    def rebuild(self) -> None:
        self.path.unlink(missing_ok=True)
        self.initialize()

    def transaction_rows(self, include_excluded: bool = True, filters: dict | None = None) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[object] = []
        if not include_excluded:
            clauses.extend(("t.excluded_reason=''", "t.unsupported_currency=0", "c.bucket NOT IN ('excluded','investment')"))
        filters = filters or {}
        for key, sql in (("date_from", "t.booking_date >= ?"), ("date_to", "t.booking_date <= ?"), ("account", "t.account = ?"), ("source", "s.source_type = ?"), ("category", "c.id = ?")):
            if filters.get(key):
                clauses.append(sql)
                params.append(filters[key])
        clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as con:
            return con.execute(
                f"""SELECT t.*, c.level1, c.level2, c.level3, c.bucket, cm.name AS canonical_merchant, s.filename, s.source_type
                FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
                LEFT JOIN canonical_merchants cm ON cm.id=t.canonical_merchant_id
                JOIN source_files s ON s.id=t.source_file_id {clause}
                ORDER BY t.booking_date DESC, t.id DESC""",
                params,
            ).fetchall()

    def merchant_review_groups(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """WITH review_rows AS (
                    SELECT t.*,
                    CASE WHEN t.amount_cents < 0 THEN 'expense' ELSE 'income' END AS direction
                    FROM transactions t
                    WHERE t.category_status='unclassified' AND t.excluded_reason=''
                    AND t.unsupported_currency=0 AND t.transaction_kind='cash' AND t.amount_cents != 0
                )
                SELECT r.merchant, r.direction, COUNT(*) AS transaction_count,
                SUM(r.amount_cents) AS amount_cents, MIN(r.booking_date) AS date_from,
                MAX(r.booking_date) AS date_to, COUNT(DISTINCT r.account) AS account_count,
                MIN(r.category_reason) AS category_reason
                FROM review_rows r
                LEFT JOIN merchant_review_progress p ON p.merchant=r.merchant AND p.direction=r.direction
                WHERE p.merchant IS NULL
                GROUP BY r.merchant, r.direction
                ORDER BY CASE
                    WHEN LOWER(r.merchant)='unknown bank transaction' THEN 0
                    WHEN MIN(r.category_reason) LIKE 'rule_conflict%' THEN 1
                    ELSE 2
                END, transaction_count DESC, r.merchant ASC"""
            ).fetchall()

    def merchant_review_group(self, merchant: str, direction: str) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """SELECT t.* FROM transactions t
                LEFT JOIN merchant_review_progress p ON p.merchant=t.merchant
                AND p.direction=CASE WHEN t.amount_cents < 0 THEN 'expense' ELSE 'income' END
                WHERE t.merchant=? AND CASE WHEN t.amount_cents < 0 THEN 'expense' ELSE 'income' END=?
                AND t.category_status='unclassified' AND t.excluded_reason=''
                AND t.unsupported_currency=0 AND t.transaction_kind='cash' AND t.amount_cents != 0
                AND p.merchant IS NULL
                ORDER BY t.booking_date ASC, t.id ASC""",
                (merchant, direction),
            ).fetchall()

    def skip_merchant_review_group(self, merchant: str, direction: str) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO merchant_review_progress(merchant,direction,status,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(merchant,direction) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at""",
                (merchant, direction, "skipped", now()),
            )

    def set_override(self, transaction_id: int, category_id: int, note: str) -> None:
        with self.connect() as con:
            before = con.execute("SELECT category_id,category_reason,category_status FROM transactions WHERE id=?", (transaction_id,)).fetchone()
            con.execute("UPDATE transactions SET category_id=?, category_reason='manual_override', category_status='manual' WHERE id=?", (category_id, transaction_id))
            con.execute(
                "INSERT INTO audit_log(transaction_id,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?)",
                (transaction_id, "category_override", json.dumps(dict(before)), json.dumps({"category_id": category_id}), note, now()),
            )

    def reconcile_paypal(self) -> dict[str, int]:
        automatic = suggested = 0
        with self.connect() as con:
            paypal_rows = con.execute(
                """SELECT t.* FROM transactions t JOIN source_files s ON s.id=t.source_file_id
                WHERE s.source_type='paypal_csv' AND t.amount_cents != 0"""
            ).fetchall()
            bank_rows = con.execute(
                """SELECT t.* FROM transactions t JOIN source_files s ON s.id=t.source_file_id
                WHERE s.source_type='deutsche_bank_pdf' AND t.amount_cents != 0"""
            ).fetchall()
            for match in reconcile_paypal_rows([dict(row) for row in paypal_rows], [dict(row) for row in bank_rows]):
                con.execute(
                    "INSERT OR IGNORE INTO reconciliations(left_transaction_id,right_transaction_id,kind,reason,confidence,status) VALUES(?,?,?,?,?,?)",
                    (match["paypal_id"], match["bank_id"], "paypal_bank", match["reason"], match["confidence"], match["status"]),
                )
                if match["status"] == "automatic":
                    con.execute("UPDATE transactions SET excluded_reason='paypal_matched' WHERE id=? AND excluded_reason=''", (match["bank_id"],))
                    automatic += 1
                else:
                    suggested += 1
        return {"automatic": automatic, "suggested": suggested}

    def reconcile_refunds(self) -> dict[str, int]:
        automatic = suggested = 0
        with self.connect() as con:
            rows = [dict(row) for row in con.execute(
                """SELECT t.* FROM transactions t
                WHERE t.unsupported_currency=0 AND t.excluded_reason != 'failed_transaction'"""
            ).fetchall()]
            for left in rows:
                candidates = []
                for right in rows:
                    if left["id"] == right["id"]:
                        continue
                    if left["amount_cents"] + right["amount_cents"] != 0:
                        continue
                    if abs((date_from_iso(left["booking_date"]) - date_from_iso(right["booking_date"])).days) > 3:
                        continue
                    if left["excluded_reason"] == "matched_refund_pair" or right["excluded_reason"] == "matched_refund_pair":
                        continue
                    score = refund_match_score(left, right)
                    if score <= 0:
                        continue
                    candidates.append((right, score))
                if not candidates:
                    continue
                candidates.sort(key=lambda item: item[1], reverse=True)
                top_score = candidates[0][1]
                top = [item for item in candidates if item[1] == top_score]
                status = "automatic" if len(top) == 1 and top_score >= 0.9 else "suggested"
                target = top[0][0]
                reason = "refund_amount_date_merchant_match" if top_score >= 0.9 else "refund_amount_date_possible_match"
                con.execute(
                    "INSERT OR IGNORE INTO reconciliations(left_transaction_id,right_transaction_id,kind,reason,confidence,status) VALUES(?,?,?,?,?,?)",
                    (min(left["id"], target["id"]), max(left["id"], target["id"]), "refund_pair", reason, top_score, status),
                )
                if status == "automatic":
                    con.execute("UPDATE transactions SET excluded_reason='matched_refund_pair' WHERE id IN (?,?) AND excluded_reason=''", (left["id"], target["id"]))
                    automatic += 1
                else:
                    suggested += 1
        return {"automatic": automatic, "suggested": suggested}

    def reconciliation_rows(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM reconciliations ORDER BY id").fetchall()

    def table_count(self, table: str) -> int:
        with self.connect() as con:
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def date_from_iso(raw: str):
    from datetime import date
    return date.fromisoformat(raw)


def refund_match_score(left: dict, right: dict) -> float:
    left_tokens = set(tokenize(left["merchant"] + " " + left["description"]))
    right_tokens = set(tokenize(right["merchant"] + " " + right["description"]))
    overlap = left_tokens & right_tokens
    if overlap:
        return 0.95
    if left.get("external_id") and left.get("external_id") == right.get("external_id"):
        return 0.95
    return 0.6 if left["currency"] == right["currency"] else 0.0


def tokenize(text: str) -> list[str]:
    import re

    return [token for token in re.findall(r"\w+", text.lower()) if len(token) > 3]
