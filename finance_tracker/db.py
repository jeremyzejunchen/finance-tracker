from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .categories import DEFAULT_CATEGORIES, LEGACY_RULES


SCHEMA_VERSION = 2


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
                CREATE TABLE IF NOT EXISTS category_rules (
                    id INTEGER PRIMARY KEY, pattern TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id),
                    priority INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(pattern, category_id)
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY, source_file_id INTEGER NOT NULL REFERENCES source_files(id),
                    booking_date TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
                    merchant TEXT NOT NULL, description TEXT NOT NULL, account TEXT NOT NULL,
                    external_id TEXT NOT NULL DEFAULT '', transaction_kind TEXT NOT NULL DEFAULT 'cash',
                    value_date TEXT NOT NULL DEFAULT '', transaction_type TEXT NOT NULL DEFAULT '',
                    source_format TEXT NOT NULL DEFAULT '', is_internal_transfer INTEGER NOT NULL DEFAULT 0,
                    is_failed_transaction INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
                    category_id INTEGER REFERENCES categories(id), category_reason TEXT NOT NULL DEFAULT 'uncategorized',
                    excluded_reason TEXT NOT NULL DEFAULT '', unsupported_currency INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS category_overrides (
                    id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL REFERENCES transactions(id),
                    category_id INTEGER NOT NULL REFERENCES categories(id), note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS reconciliations (
                    id INTEGER PRIMARY KEY, left_transaction_id INTEGER NOT NULL REFERENCES transactions(id),
                    right_transaction_id INTEGER NOT NULL REFERENCES transactions(id), kind TEXT NOT NULL,
                    confidence REAL NOT NULL, status TEXT NOT NULL DEFAULT 'suggested',
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
                CREATE INDEX IF NOT EXISTS idx_txn_category ON transactions(category_id);
                CREATE INDEX IF NOT EXISTS idx_txn_source ON transactions(source_file_id);
                """
            )
            con.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)", (SCHEMA_VERSION, now()))
            existing_columns = {row["name"] for row in con.execute("PRAGMA table_info(transactions)")}
            for name, definition in {
                "value_date": "TEXT NOT NULL DEFAULT ''", "transaction_type": "TEXT NOT NULL DEFAULT ''",
                "source_format": "TEXT NOT NULL DEFAULT ''", "is_internal_transfer": "INTEGER NOT NULL DEFAULT 0",
                "is_failed_transaction": "INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in existing_columns:
                    con.execute(f"ALTER TABLE transactions ADD COLUMN {name} {definition}")
            for level1, level2, level3, bucket in DEFAULT_CATEGORIES:
                con.execute("INSERT OR IGNORE INTO categories(level1,level2,level3,bucket) VALUES(?,?,?,?)", (level1, level2, level3, bucket))
            for leaf, pattern in LEGACY_RULES.items():
                category = con.execute("SELECT id FROM categories WHERE level3=? ORDER BY id DESC LIMIT 1", (leaf,)).fetchone()
                if category:
                    con.execute("INSERT OR IGNORE INTO category_rules(pattern,category_id,priority,active) VALUES(?,?,100,1)", (pattern, category["id"]))

    def source_exists(self, sha256: str) -> bool:
        with self.connect() as con:
            return con.execute("SELECT 1 FROM source_files WHERE sha256=?", (sha256,)).fetchone() is not None

    def category_rows(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM categories WHERE active=1 ORDER BY level1, level2, level3").fetchall()

    def add_category(self, level1: str, level2: str, level3: str, bucket: str) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO categories(level1,level2,level3,bucket) VALUES(?,?,?,?)", (level1.strip(), level2.strip(), level3.strip(), bucket))

    def add_rule(self, pattern: str, category_id: int, priority: int = 100) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO category_rules(pattern,category_id,priority,active) VALUES(?,?,?,1)", (pattern.strip(), category_id, priority))

    def active_rules(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("""SELECT r.*, c.level1, c.level2, c.level3, c.bucket
                FROM category_rules r JOIN categories c ON c.id=r.category_id
                WHERE r.active=1 ORDER BY r.priority ASC, r.id ASC""").fetchall()

    def rule_rows(self) -> list[sqlite3.Row]:
        return self.active_rules()

    def write_import(self, source: dict, transactions: list[dict]) -> dict:
        with self.connect() as con:
            existing = con.execute("SELECT id FROM source_files WHERE sha256=?", (source["sha256"],)).fetchone()
            if existing:
                return {"duplicate_source": True, "inserted": 0}
            cur = con.execute("""INSERT INTO source_files(path,filename,source_type,sha256,imported_at,parser_version,record_count)
                VALUES(?,?,?,?,?,?,?)""", (source["path"], source["filename"], source["source_type"], source["sha256"], now(), "0.1.0", len(transactions)))
            source_id = cur.lastrowid
            inserted = 0
            rejected = 0
            for item in transactions:
                if item["unsupported_currency"]:
                    rejected += 1
                try:
                    con.execute("""INSERT INTO transactions(source_file_id,booking_date,amount_cents,currency,merchant,description,account,external_id,transaction_kind,value_date,transaction_type,source_format,is_internal_transfer,is_failed_transaction,raw_json,fingerprint,category_id,category_reason,excluded_reason,unsupported_currency,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        source_id, item["booking_date"], item["amount_cents"], item["currency"], item["merchant"], item["description"], item["account"],
                        item["external_id"], item["transaction_kind"], item["value_date"], item["transaction_type"], item["source_format"], item["is_internal_transfer"], item["is_failed_transaction"], json.dumps(item["raw"], ensure_ascii=False), item["fingerprint"], item["category_id"],
                        item["category_reason"], item["excluded_reason"], item["unsupported_currency"], now()))
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass
            con.execute("INSERT INTO import_batches(source_file_id,imported_at,accepted_count,rejected_count) VALUES(?,?,?,?)", (source_id, now(), inserted, rejected))
            return {"duplicate_source": False, "inserted": inserted, "rejected": rejected}

    def write_import_batch(self, imports: list[tuple[dict, list[dict]]]) -> list[dict]:
        """Write every non-duplicate source in one SQLite transaction."""
        results: list[dict] = []
        with self.connect() as con:
            run = con.execute("INSERT INTO import_runs(imported_at,file_count) VALUES(?,?)", (now(), len(imports)))
            run_id = run.lastrowid
            for source, transactions in imports:
                existing = con.execute("SELECT id FROM source_files WHERE sha256=?", (source["sha256"],)).fetchone()
                if existing:
                    results.append({"duplicate_source": True, "inserted": 0, "rejected": 0})
                    continue
                cur = con.execute("""INSERT INTO source_files(path,filename,source_type,sha256,imported_at,parser_version,record_count)
                    VALUES(?,?,?,?,?,?,?)""", (source["path"], source["filename"], source["source_type"], source["sha256"], now(), "0.2.0", len(transactions)))
                source_id = cur.lastrowid
                inserted = rejected = 0
                for item in transactions:
                    rejected += int(bool(item["unsupported_currency"]))
                    try:
                        con.execute("""INSERT INTO transactions(source_file_id,booking_date,amount_cents,currency,merchant,description,account,external_id,transaction_kind,value_date,transaction_type,source_format,is_internal_transfer,is_failed_transaction,raw_json,fingerprint,category_id,category_reason,excluded_reason,unsupported_currency,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (source_id, item["booking_date"], item["amount_cents"], item["currency"], item["merchant"], item["description"], item["account"], item["external_id"], item["transaction_kind"], item["value_date"], item["transaction_type"], item["source_format"], item["is_internal_transfer"], item["is_failed_transaction"], json.dumps(item["raw"], ensure_ascii=False), item["fingerprint"], item["category_id"], item["category_reason"], item["excluded_reason"], item["unsupported_currency"], now()))
                        inserted += 1
                    except sqlite3.IntegrityError:
                        pass
                con.execute("INSERT INTO import_batches(source_file_id,imported_at,accepted_count,rejected_count,notes) VALUES(?,?,?,?,?)", (source_id, now(), inserted, rejected, f"unified_batch:{run_id}"))
                results.append({"duplicate_source": False, "inserted": inserted, "rejected": rejected})
        return results

    def rebuild(self) -> None:
        """Delete derived SQLite state only; source statements are never touched."""
        self.path.unlink(missing_ok=True)
        self.initialize()

    def transaction_rows(self, include_excluded: bool = True, filters: dict | None = None) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list = []
        if not include_excluded:
            clauses.extend(("t.excluded_reason=''", "t.unsupported_currency=0", "c.bucket NOT IN ('excluded','investment')"))
        filters = filters or {}
        for key, sql in (("date_from", "t.booking_date >= ?"), ("date_to", "t.booking_date <= ?"), ("account", "t.account = ?"), ("source", "s.source_type = ?"), ("category", "c.id = ?")):
            if filters.get(key):
                clauses.append(sql); params.append(filters[key])
        for key, sql in (("min_amount", "t.amount_cents >= ?"), ("max_amount", "t.amount_cents <= ?")):
            if filters.get(key) not in (None, ""):
                clauses.append(sql); params.append(int(float(filters[key]) * 100))
        clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as con:
            return con.execute(f"""SELECT t.*, c.level1, c.level2, c.level3, c.bucket, s.filename, s.source_type
                FROM transactions t LEFT JOIN categories c ON c.id=t.category_id
                JOIN source_files s ON s.id=t.source_file_id {clause} ORDER BY t.booking_date DESC, t.id DESC""", params).fetchall()

    def set_override(self, transaction_id: int, category_id: int, note: str) -> None:
        with self.connect() as con:
            before = con.execute("SELECT category_id,category_reason FROM transactions WHERE id=?", (transaction_id,)).fetchone()
            con.execute("UPDATE category_overrides SET active=0 WHERE transaction_id=?", (transaction_id,))
            con.execute("INSERT INTO category_overrides(transaction_id,category_id,note,created_at) VALUES(?,?,?,?)", (transaction_id, category_id, note, now()))
            con.execute("UPDATE transactions SET category_id=?, category_reason='manual_override' WHERE id=?", (category_id, transaction_id))
            con.execute("INSERT INTO audit_log(transaction_id,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?)", (transaction_id, "category_override", json.dumps(dict(before)), json.dumps({"category_id": category_id}), note, now()))

    def reconcile_paypal(self) -> dict[str, int]:
        """Link PayPal purchases to their corresponding bank direct debits.

        Only an unambiguous amount/date candidate is excluded automatically. Ambiguous
        candidates remain visible as suggestions in the review queue.
        """
        automatic = suggested = 0
        with self.connect() as con:
            paypal_rows = con.execute("""SELECT t.* FROM transactions t JOIN source_files s ON s.id=t.source_file_id
                WHERE s.source_type='paypal_csv' AND t.amount_cents < 0""").fetchall()
            bank_rows = con.execute("""SELECT t.* FROM transactions t JOIN source_files s ON s.id=t.source_file_id
                WHERE s.source_type='deutsche_bank_pdf' AND t.amount_cents < 0
                AND upper(t.merchant || ' ' || t.description) LIKE '%PAYPAL%'""").fetchall()
            for paypal in paypal_rows:
                candidates = [bank for bank in bank_rows if bank["amount_cents"] == paypal["amount_cents"]
                              and abs((date_from_string(bank["booking_date"]) - date_from_string(paypal["booking_date"])).days) <= 5]
                if not candidates:
                    continue
                candidates.sort(key=lambda bank: abs((date_from_string(bank["booking_date"]) - date_from_string(paypal["booking_date"])).days))
                bank = candidates[0]
                confidence = 0.95 if len(candidates) == 1 else 0.60
                status = "automatic" if confidence >= 0.9 else "suggested"
                con.execute("INSERT OR IGNORE INTO reconciliations(left_transaction_id,right_transaction_id,kind,confidence,status) VALUES(?,?,?,?,?)", (paypal["id"], bank["id"], "paypal_bank", confidence, status))
                if status == "automatic":
                    con.execute("UPDATE transactions SET excluded_reason='paypal_matched' WHERE id=? AND excluded_reason=''", (bank["id"],))
                    automatic += 1
                else:
                    suggested += 1
        return {"automatic": automatic, "suggested": suggested}


def date_from_string(raw: str):
    from datetime import date
    return date.fromisoformat(raw)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
