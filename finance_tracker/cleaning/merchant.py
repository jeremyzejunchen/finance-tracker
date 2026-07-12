from __future__ import annotations

import re


WHITESPACE_RE = re.compile(r"\s+")


def normalize_merchant(raw: str) -> str:
    return WHITESPACE_RE.sub(" ", (raw or "").strip())[:160]


def extract_merchant_from_payment_details(detail_lines: list[str]) -> str:
    for line in detail_lines:
        match = re.search(r"Payment details\s+(.+?)//", line)
        if match:
            return normalize_merchant(match.group(1))
        match = re.match(r"^([^/]+?)//", line)
        if match:
            return normalize_merchant(match.group(1))
    return normalize_merchant(detail_lines[0] if detail_lines else "")
