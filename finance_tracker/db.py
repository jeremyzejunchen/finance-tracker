from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .categories import DEFAULT_CATEGORIES
from .reconciliation import reconcile_paypal_rows


SCHEMA_VERSION = 3


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
                CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(booking_date);
                CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(source_file_id);
                CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category_id);
                """
            )
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
        return []

    def rule_rows(self) -> list[sqlite3.Row]:
        return []

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
                        is_failed_transaction,raw_json,fingerprint,category_id,category_status,category_reason,excluded_reason,unsupported_currency,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            source_id, item["booking_date"], item["value_date"], item["amount_cents"], item["currency"],
                            item["merchant_raw"], item["merchant"], item["description"], item["account"], item["external_id"],
                            item["transaction_kind"], item["transaction_type"], item["source_format"], item["source_record_index"],
                            item["source_record_key"], item["is_internal_transfer"], item["is_failed_transaction"],
                            json.dumps(item["raw"], ensure_ascii=False), item["fingerprint"], item["category_id"],
                            item["category_status"], item["category_reason"], item["excluded_reason"], item["unsupported_currency"], now(),
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
                f"""SELECT t.*, c.level1, c.level2, c.level3, c.bucket, s.filename, s.source_type
                FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
                JOIN source_files s ON s.id=t.source_file_id {clause}
                ORDER BY t.booking_date DESC, t.id DESC""",
                params,
            ).fetchall()

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


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
