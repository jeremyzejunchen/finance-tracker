from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from .app import build_server
from .runtime import migrate_legacy_runtime_data, project_runtime_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="启动离线财务管理应用")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--database", type=Path,
                        help="SQLite 数据库路径；建议使用本地磁盘，不要放在 NAS/网络共享目录。")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    paths = project_runtime_paths()
    if args.database is None:
        migrate_legacy_runtime_data(paths)
    database_path = args.database or paths.database_path
    server = build_server(args.host, args.port, database_path.resolve())
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
