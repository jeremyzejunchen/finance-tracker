from __future__ import annotations

import argparse
import os
import webbrowser
from pathlib import Path

from .app import build_server


def main() -> None:
    parser = argparse.ArgumentParser(description="启动离线财务管理应用")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    default_database = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FinanceTracker" / "finance_tracker.sqlite3"
    parser.add_argument("--database", type=Path, default=default_database,
                        help="SQLite 数据库路径；建议使用本地磁盘，不要放在 NAS/网络共享目录。")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = build_server(args.host, args.port, args.database.resolve())
    url = f"http://{args.host}:{args.port}/"
    print(f"Finance Tracker 已启动：{url}")
    print("按 Ctrl+C 停止服务。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
