# Finance Tracker

完全离线的个人账单导入、清洗和收支可视化工具。支持 Deutsche Bank PDF、PayPal CSV 与 Trade Republic CSV；原始账单只读，数据库仅保存提取后的标准化记录和审计信息。

## 启动

```powershell
python -m finance_tracker
```

默认数据库位于本机 `%LOCALAPPDATA%\FinanceTracker\finance_tracker.sqlite3`，不放在 NAS 或网络共享目录。可通过 `--database D:\FinanceTracker\finance.sqlite3` 指定其他本地磁盘路径。

浏览器打开 `http://127.0.0.1:8765` 后，可在“导入”页选择账单、预览并确认；导入文件不会被复制或修改。

## 开发验证

```powershell
python -m unittest discover -s tests -v
```

本项目不使用 CDN、遥测或云端 API。不要提交 PDF、CSV、SQLite 数据库、生成报告或包含个人账单标识的信息。
