from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


class ImportErrorForUser(ValueError):
    pass


def read_csv(content: bytes) -> list[dict[str, str]]:
    text = ""
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise ImportErrorForUser("CSV 编码不受支持。")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def normalize_keys(row: dict[str, str]) -> dict[str, str]:
    return {re.sub(r"\s+", " ", (key or "").strip().lower()): (val or "").strip() for key, val in row.items()}


def value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        if row.get(key):
            return row[key]
    return ""


def parse_date(raw: str):
    raw = raw.strip().split(" ")[0]
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(raw: str) -> Decimal | None:
    value = raw.replace("EUR", "").replace("€", "").replace(" ", "").strip()
    if not value:
        return None
    negative = value.endswith("-") or value.startswith("-")
    positive = value.startswith("+")
    value = value.strip("+-")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        parsed = Decimal(value)
        return -parsed if negative else parsed if positive or not negative else parsed
    except InvalidOperation:
        return None
