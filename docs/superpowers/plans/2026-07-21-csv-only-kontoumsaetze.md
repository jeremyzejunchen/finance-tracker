# CSV-only Kontoumsaetze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 仅支持 CSV 账单，使用 Kontoumsaetze CSV 作为 ME 银行流水的正式来源，并从本地账本中安全移除旧 PDF 来源数据。

**Architecture:** 导入器按 CSV 内容识别格式，再由扫描服务按 `-czj.csv`/`-cr.csv` 覆盖账户归属。新 Kontoumsaetze 导入器负责表头定位、字段提取和商户清洗；数据库初始化负责备份和一次性、原子地删除 `deutsche_bank_pdf` 来源数据。PDF 代码与依赖路径从产品中移除。

**Tech Stack:** Python 3.14（仅 `.venv-phase1`）、SQLite、标准库 CSV/HTTP、原生 JavaScript、Node UI 测试、Playwright CLI。

## Global Constraints

- 所有 PowerShell-Python 调用及脚本必须自行设置 `PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8`。
- `银行流水/` 的全部内容、真实账户元数据与交易数据必须被 Git 忽略，绝不出现在 Git、日志、夹具、截图或浏览器 trace。
- 只扫描和导入 `.csv`；PDF 只能保留在用户磁盘，不能被应用发现或读取。
- 任何 `*-czj.csv` 强制为 ME，任何 `*-cr.csv` 强制为 WIFE；内容识别不能覆盖该账户归属。
- Kontoumsaetze 仅对 `Kontoumsaetze*-czj.csv` 生效；其他 CSV 保持各自内容识别路径。
- PDF 账本移除必须先备份，删除必须在一个 SQLite 事务中完成且可幂等；其他 CSV 数据必须保留。
- 测试和浏览器验证只能使用合成数据和 `.tmp/` 临时数据库。
- 完成前运行 doctor、完整测试、`git diff --check`、完整 diff 自审和只读汇总验收；通过后自动提交、推送并建立/更新草稿 PR。

---

### Task 1: 隔离真实账单并收缩目录扫描为 CSV

**Files:**
- Modify: `.gitignore`
- Modify: `finance_tracker/statement_directory.py`
- Modify: `tests/test_finance_tracker.py`

**Interfaces:**
- Produces: `StatementDirectoryScanner.scan()` 仅返回 CSV `StatementFile`。
- Preserves: `-czj.csv -> ME`、`-cr.csv -> WIFE`、其他 CSV 需要人工账户选择。

- [ ] **Step 1: 写入失败测试。**

```python
def test_statement_directory_scan_ignores_pdf_and_preserves_csv_account_suffixes(self):
    root = Path(self.directory.name) / "银行流水"
    root.mkdir()
    (root / "old.pdf").write_bytes(b"synthetic-pdf")
    (root / "bank-czj.csv").write_bytes(b"synthetic-czj")
    (root / "bank-cr.csv").write_bytes(b"synthetic-cr")
    rows = {row.relative_path: row for row in StatementDirectoryScanner(root, self.db.source_exists).scan()}
    self.assertNotIn("old.pdf", rows)
    self.assertEqual("ME", rows["bank-czj.csv"].account)
    self.assertEqual("WIFE", rows["bank-cr.csv"].account)
```

- [ ] **Step 2: 运行失败测试。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker.FinanceTrackerTests.test_statement_directory_scan_ignores_pdf_and_preserves_csv_account_suffixes -v`

Expected: FAIL，因为扫描器仍返回 PDF。

- [ ] **Step 3: 最小实现。**

将扫描器文件后缀条件改为仅 `.csv`；移除 PDF 的 ME 归属分支。将 `.gitignore` 的根级 `银行流水/*.pdf` 与 `银行流水/*.csv` 改为单条递归规则 `银行流水/**`，保留目录本身但忽略所有真实账单内容。

- [ ] **Step 4: 验证并提交。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker -k statement_directory -v`

Expected: PASS。

Commit: `git add -- .gitignore finance_tracker/statement_directory.py tests/test_finance_tracker.py; git commit -m "仅扫描 CSV 账单目录"`

### Task 2: 新增 Kontoumsaetze CSV 解析器与安全内容识别

**Files:**
- Create: `finance_tracker/importers/kontoumsaetze.py`
- Modify: `finance_tracker/importers/__init__.py`
- Modify: `tests/test_finance_tracker.py`
- Create: `tests/fixtures/kontoumsaetze-czj.csv`

**Interfaces:**
- Produces: `parse_kontoumsaetze_csv(content: bytes, filename: str) -> list[ParsedTransaction]`
- Produces source type `kontoumsaetze_csv` only for `Kontoumsaetze*-czj.csv` with the required transaction header.

- [ ] **Step 1: 创建匿名合成夹具和失败测试。**

夹具使用 UTF-8 BOM、前 7 行非敏感占位元数据、第 8 行 18 个真实列名，交易行使用匿名商户和无效 IBAN/BIC 占位值。测试断言日期、金额、EUR、交易类型、商户优先级、source type 和 `source_record_key`；不对 `raw` 输出任何敏感字段。

```python
transactions = parse_file("Kontoumsaetze_synthetic-czj.csv", fixture, self.config)
self.assertEqual("kontoumsaetze_csv", transactions[0])
self.assertEqual("ME", transactions[1][0].account)
self.assertEqual("SYNTHETIC MARKET", transactions[1][0].merchant_normalized)
```

- [ ] **Step 2: 运行失败测试。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker -k kontoumsaetze -v`

Expected: FAIL，因为 source type 和解析器不存在。

- [ ] **Step 3: 实现解析与识别。**

在 `kontoumsaetze.py` 中严格解码 UTF-8 BOM，使用 `csv.DictReader` 从包含 `Buchungstag`、`Betrag`、`Währung` 的表头开始读取分号 CSV。日期使用 `Buchungstag`/`Wert`；商户优先 `Begünstigter / Auftraggeber`，为空或通用标签时从 `Verwendungszweck` 选择；金额使用 `Betrag`，币种使用 `Währung`。只将非敏感提取字段放入 `raw`，排除 IBAN、BIC、客户/授权/债权人参考。缺少标题或没有有效交易时抛 `ImportErrorForUser`。

在 `detect_source()` 中先拒绝 `.pdf`，再仅在文件名匹配 `Kontoumsaetze*-czj.csv` 且内容有必需表头时返回 `kontoumsaetze_csv`；保留 PayPal 与 Trade Republic 内容识别。

- [ ] **Step 4: 增加错误和账户覆盖测试。**

```python
with self.assertRaisesRegex(ImportErrorForUser, "Kontoumsaetze"):
    parse_file("Kontoumsaetze_bad-czj.csv", b"not a statement", self.config)
preview = self.service.preview("Kontoumsaetze_synthetic-czj.csv", fixture)
self.assertTrue(all(tx.account == "ME" for tx in preview.transactions))
```

- [ ] **Step 5: 验证并提交。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker -k kontoumsaetze -v`

Expected: PASS。

Commit: `git add -- finance_tracker/importers/kontoumsaetze.py finance_tracker/importers/__init__.py tests/test_finance_tracker.py tests/fixtures/kontoumsaetze-czj.csv; git commit -m "支持 Kontoumsaetze CSV 导入"`

### Task 3: 备份后原子移除 PDF 账本数据

**Files:**
- Modify: `finance_tracker/db.py`
- Modify: `tests/test_finance_tracker.py`

**Interfaces:**
- Produces: `Database.remove_pdf_source_data() -> dict[str, int]`
- Produces: 幂等迁移审计 action `remove_pdf_source_data`，只含来源/计数摘要。

- [ ] **Step 1: 写入失败测试。**

```python
result = self.db.remove_pdf_source_data()
self.assertEqual(1, result["source_files_removed"])
self.assertEqual(1, result["transactions_removed"])
self.assertEqual(0, self._source_count("deutsche_bank_pdf"))
self.assertEqual(1, self._source_count("paypal_csv"))
self.assertEqual(result, self.db.remove_pdf_source_data())
```

- [ ] **Step 2: 运行失败测试。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker.FinanceTrackerTests.test_remove_pdf_source_data_is_atomic_and_idempotent -v`

Expected: FAIL，因为移除入口不存在。

- [ ] **Step 3: 实现备份和事务删除。**

在初始化 CSV-only schema 迁移前调用既有备份机制，在项目 `exports/backups/schema/` 保存数据库副本。单一 `connect()` 事务中选择 PDF `source_files` id，删除引用这些交易的 `reconciliations`、`audit_log`、`transactions`、相关 `import_batches` 与 `source_files`；插入一条没有 transaction id 的汇总 audit。无 PDF 来源时返回全零计数并不再产生新备份。

- [ ] **Step 4: 验证回滚与保留其他 CSV。**

对合成数据库触发外键失败，断言 PDF 交易仍存在；成功后断言 backup 存在、PayPal/Trade Republic CSV 交易保持不变、第二次调用不再改动。

- [ ] **Step 5: 验证并提交。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker -k remove_pdf_source_data -v`

Expected: PASS。

Commit: `git add -- finance_tracker/db.py tests/test_finance_tracker.py; git commit -m "移除 PDF 账本来源数据"`

### Task 4: 删除 PDF 产品路径和依赖假设

**Files:**
- Delete: `finance_tracker/importers/deutsche_bank.py`
- Modify: `finance_tracker/importers/__init__.py`
- Modify: `finance_tracker/audit.py`
- Modify: `finance_tracker/db.py`
- Modify: `finance_tracker/static/app.js`
- Modify: `finance_tracker/app.py`
- Modify: `scripts/doctor.ps1`
- Modify: `README.md`, `docs/development-environment.md`, `docs/import-audit.md`, `docs/phase-1-parser-parity.md`
- Modify: `tests/test_finance_tracker.py`, `tests/test_ui.mjs`
- Delete: PDF-only fixtures under `tests/fixtures/`

**Interfaces:**
- `parse_file()` rejects PDF with a user-facing CSV-only error.
- PayPal-bank reconciliation becomes unavailable unless it can be expressed entirely with retained CSV sources; no stale `deutsche_bank_pdf` source checks remain.

- [ ] **Step 1: 替换或删除 PDF 专属测试。**

删除 Deutsche Bank parser-parity、PDF audit、PDF directory and UI fixture assertions。新增：

```python
with self.assertRaisesRegex(ImportErrorForUser, "CSV"):
    parse_file("old.pdf", b"synthetic", self.config)
```

- [ ] **Step 2: 运行失败测试。**

Run: `& .\.venv-phase1\Scripts\python.exe -m unittest tests.test_finance_tracker -k pdf -v`

Expected: FAIL 或仍有 PDF 专属实现引用，作为清除清单。

- [ ] **Step 3: 最小清除。**

删除 PDF parser import/文件；`detect_source()` 对 PDF 立即抛 CSV-only 错误；UI 文件选择 `accept` 改为 `.csv`；目录和文档删除 PDF 描述；doctor 不再探测 PyMuPDF。删除依赖 PDF 来源的 PayPal-bank reconciliation 分支，而不影响其他 CSV 的去重、退款和商户规则。

- [ ] **Step 4: 验证无残留。**

Run: `rg -n "deutsche_bank_pdf|parse_deutsche_bank|PyMuPDF|\.pdf" finance_tracker tests scripts README.md docs/development-environment.md docs/import-audit.md docs/phase-1-parser-parity.md`

Expected: 不得有可执行 PDF 导入路径。历史规格和旧实施计划不纳入此检查，可保留其 PDF 历史记录。

- [ ] **Step 5: 验证并提交。**

Run: `pwsh -NoProfile -File .\scripts\doctor.ps1; pwsh -NoProfile -File .\scripts\test.ps1`

Expected: PASS，且 doctor 不依赖 PyMuPDF。

Commit: `git add -- finance_tracker/importers finance_tracker/audit.py finance_tracker/db.py finance_tracker/static/app.js finance_tracker/app.py scripts/doctor.ps1 README.md docs/development-environment.md docs/import-audit.md docs/phase-1-parser-parity.md tests/test_finance_tracker.py tests/test_ui.mjs tests/fixtures; git commit -m "移除 PDF 导入路径"`

### Task 5: CSV-only 浏览器验证、真实汇总验收与发布

**Files:**
- Create: `tests/test_kontoumsaetze_browser.mjs`
- Modify: `scripts/test.ps1`（仅在当前脚本未运行该测试时）
- Modify: `02_问题与报错日志.md`（仅在实际发生新错误时）

- [ ] **Step 1: 创建合成浏览器测试。**

在 `.tmp/kontoumsaetze-browser/` 建立合成 `Kontoumsaetze*-czj.csv`、`*-cr.csv` 和临时 SQLite；启动本地服务器。验证扫描不列出 PDF、两个 CSV 账户归属正确、Kontoumsaetze 可预览和确认、PDF 手动上传被拒绝。关闭服务并删除临时目录。

- [ ] **Step 2: 运行浏览器测试。**

Run: `node .\tests\test_kontoumsaetze_browser.mjs`

Expected: exit code 0，且无可提交截图/trace/数据库。

- [ ] **Step 3: 完整验证和只读汇总验收。**

Run: `pwsh -NoProfile -File .\scripts\doctor.ps1; pwsh -NoProfile -File .\scripts\test.ps1; git diff --check; git status --short; git --no-pager diff`

Expected: 全部通过，真实账单保持忽略。

随后仅对项目本地数据库读取并报告：PDF 来源交易数、CSV 来源交易数、待复核数、商户覆盖率和优先级数量；不输出交易、账户、文件名或金额明细。

- [ ] **Step 4: 自审、提交、推送与草稿 PR。**

确认真实数据未被暂存、PDF 原件未删除、CSV-only 检测无绕过、迁移备份与幂等已验证后，选择性暂存本 Issue 代码、合成夹具、文档、计划和实际调试日志；排除 `.superpowers/`、`.playwright-cli/`、`银行流水/` 及旧 Issue #18 未跟踪文件。创建一个 focused commit，推送 `codex/csv-only-kontoumsaetze`，以 `codex/issue-17-statement-directory-scan` 为 base 创建/更新草稿 PR，并报告 commit、PR、验证和汇总验收。
