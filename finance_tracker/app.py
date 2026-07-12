from __future__ import annotations

import json
import mimetypes
import re
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import Database
from .importers import ImportErrorForUser
from .services import FinanceService


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


def build_server(host: str, port: int, database_path: Path) -> ThreadingHTTPServer:
    database = Database(database_path)
    database.initialize()
    service = FinanceService(database)

    class Handler(AppHandler):
        db = database
        finance = service

    return ThreadingHTTPServer((host, port), Handler)


class AppHandler(BaseHTTPRequestHandler):
    db: Database
    finance: FinanceService
    server_version = "FinanceTracker/0.1"

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/static/"):
            return self.serve_static(path.removeprefix("/static/"))
        if path == "/api/report":
            query = {key: value[-1] for key, value in parse_qs(urlparse(self.path).query).items()}
            return self.json_response(self.finance.report(query))
        if path == "/api/transactions":
            return self.json_response([dict(row) for row in self.db.transaction_rows()])
        if path == "/api/categories":
            return self.json_response([dict(row) for row in self.db.category_rows()])
        if path == "/api/rules":
            return self.json_response([dict(row) for row in self.db.rule_rows()])
        if path in ("/", "/import", "/transactions", "/review", "/categories"):
            return self.html_response(render_page(path))
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/import/preview":
                fields, files = parse_multipart(self)
                uploads = files.get("statements", []) or files.get("statement", [])
                if not uploads:
                    raise ImportErrorForUser("请选择账单文件。")
                return self.json_response(self.finance.preview_many(uploads))
            if path == "/api/import/confirm":
                body = self.json_body()
                return self.json_response(self.finance.confirm_many(body["items"]))
            if path == "/api/rebuild":
                body = self.json_body()
                if body.get("confirmation") != "重建本地数据":
                    raise ValueError("请输入“重建本地数据”确认不可逆操作。")
                self.db.rebuild()
                self.finance.previews.clear()
                return self.json_response({"ok": True})
            if path == "/api/transactions/category":
                body = self.json_body()
                self.db.set_override(int(body["transaction_id"]), int(body["category_id"]), str(body.get("note", "")))
                return self.json_response({"ok": True})
            if path == "/api/categories":
                body = self.json_body()
                self.db.add_category(body["level1"], body["level2"], body["level3"], body["bucket"])
                return self.json_response({"ok": True}, HTTPStatus.CREATED)
            if path == "/api/rules":
                body = self.json_body()
                self.db.add_rule(body["pattern"], int(body["category_id"]), int(body.get("priority", 100)))
                return self.json_response({"ok": True}, HTTPStatus.CREATED)
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, ImportErrorForUser) as error:
            self.json_response({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def json_response(self, payload, status=HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def html_response(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, relative: str) -> None:
        candidate = (STATIC / relative).resolve()
        if STATIC not in candidate.parents or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args):
        # No transaction data is written to request logs.
        print(f"{self.address_string()} - {fmt % args}")


def parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], dict[str, list[dict]]]:
    content_type = handler.headers.get("Content-Type", "")
    match = re.search(r"boundary=([^;]+)", content_type)
    if not match:
        raise ValueError("请求必须使用 multipart/form-data。")
    boundary = match.group(1).strip('"').encode()
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    fields: dict[str, str] = {}
    files: dict[str, list[dict]] = {}
    for part in raw.split(b"--" + boundary):
        if b"Content-Disposition" not in part or b"\r\n\r\n" not in part:
            continue
        headers, body = part.split(b"\r\n\r\n", 1)
        # Each normal multipart part ends in CRLF before the next delimiter.
        # Do not strip arbitrary bytes: a statement may legitimately end in '-'.
        if body.endswith(b"\r\n"):
            body = body[:-2]
        disposition = headers.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match:
            files.setdefault(name, []).append({"filename": Path(filename_match.group(1)).name, "content": body})
        else:
            fields[name] = body.decode("utf-8", errors="replace")
    return fields, files


def render_page(path: str) -> str:
    title = {"/": "收支报表", "/import": "导入账单", "/transactions": "交易管理", "/review": "待复核", "/categories": "分类管理"}[path]
    nav = [("/", "报表"), ("/import", "导入"), ("/transactions", "交易"), ("/review", "复核"), ("/categories", "分类")]
    links = "".join(f'<a class="{"active" if href == path else ""}" href="{href}">{label}</a>' for href, label in nav)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · Finance Tracker</title><link rel="stylesheet" href="/static/style.css"></head>
    <body data-page="{escape(path)}"><header><div><strong>Finance Tracker</strong><span>本地离线账单管理</span></div><nav>{links}</nav></header><main><h1>{title}</h1><div id="app"></div></main><script src="/static/app.js"></script></body></html>"""
