# CLAUDE.md

本项目使用中文交流。所有回复和文档用中文。

## 概述

德国银行账户的个人财务仪表盘。从 Deutsche Bank PDF 流水单中解析交易数据，分类后生成自包含的 HTML 报告（三页签 SPA）。

## 常用命令

```
# 强制重新解析所有 PDF 并生成报告
python parse_bank_pdfs.py --force

# PDF 未变动时使用缓存
python parse_bank_pdfs.py

# 筛选特定月份
python parse_bank_pdfs.py --month 2025-06

# 一键：解析 + 打开报告
一键生成账单.bat
```

依赖：`PyMuPDF` (fitz), `plotly`。安装：`pip install PyMuPDF plotly`。

## 架构

```
parse_bank_pdfs.py              ← 主脚本。所有 HTML/CSS/JS 都在 build_report() 中生成
  ├── parse_transactions_format()       → PDF 格式 "Transactions_"（1-3 月）
  ├── parse_account_statement_format()  → PDF 格式 "Account_statement_"（5-6 月）
  ├── parse_trade_republic_csv()        → CSV 格式 Trade Republic 导出
  ├── parse_paypal_csv()                → CSV 格式 PayPal 导出（英文/德文）
  ├── match_paypal_to_bank()            → PayPal 交易与银行流水匹配去重
  ├── categorize()               → 通过关键字匹配给每笔交易分配分类
  ├── build_report()             → 生成 bank_summary_2025.html（所有 UI 在此）
  └── post_process()             → 商户名清洗 + 去重

bank_transactions.json           ← 缓存（932 笔交易）。--force 或 PDF 变动时删除重建
bank_summary_2025.html           ← 生成的输出（~5MB，Plotly.js 内嵌 + JSON 数据内嵌）

银行流水/*.pdf                   ← 源 PDF 银行流水（18 个文件，2025 年 1 月 ~ 2026 年 6 月）
银行流水/Paypal-*.csv            ← PayPal 一年流水（支持英文/德文格式，自动匹配去重）
银行流水/transactions_*.csv      ← Trade Republic CSV 导出
记账导出数据/鲨鱼记账明细*.csv    ← 手动记账导出（独立系统，本脚本不使用）
```

## build_report() 生成的 HTML 结构

三页签 SPA，全部内嵌在单个 HTML 文件中：

### 报表页签（默认激活）
- **筛选栏**：年份下拉（默认最新年份）、月份下拉、分类下拉、描述搜索、金额范围
- **动态 KPI 卡**：总收入 / 总支出 / 净额 / 交易笔数，随筛选实时更新
- **饼图 × 2**：支出分类占比 + 收入分类占比（Plotly 环形图，标签在外部，图例在右侧）
- **分类明细表**：按分类汇总金额、占比、笔数
- **年度趋势图**：每年独立一张（柱状收入+支出 + 折线结余），切换年份自动切换图表
- **年度汇总**：年度 KPI + 支出/收入分类占比表 + 12 个月月度对比表
- **交易明细表**：搜索、分类筛选（顶部 pill 按钮）、列排序

### 图表页签
- **月度支出分类**（堆叠柱状图）
- **月度收支对比**（分组柱状图）
- **累计净额走势**（面积折线图）

### 设置页签
- **分类管理**：支出/收入分类的增删改，localStorage 持久化
- **固定支出规则**：按分类 + 描述关键字设定规则
- **描述标签**：增删管理
- **JSON 导出**：导出完整数据（交易+分类+标签+规则）

### 技术细节
- Plotly.js 通过 `max(scripts, key=len)` 提取空 Figure 的最大 `<script>` 标签内嵌（~4.8MB）
- 交易数据作为 JSON 内嵌在 `<script>const RAW_TRANSACTIONS = [...];</script>` 中
- JS 加载时将银行流水标准化为 `{date, type, category, amount, merchant, description}` 格式
- 用户偏好（分类、标签、固定支出规则）通过 localStorage 持久化，key 为 `bankPrefs`
- Font Awesome 6.2.1 通过 CDN 加载图标
- CSS 全部自定义，无外部框架依赖
- 深色/明亮模式通过右上角按钮手动切换，偏好保存在 localStorage（`bankTheme`），默认深色。CSS 使用 `[data-theme="dark"]` 选择器控制

## 分类规则

分类通过商户名的忽略大小写子串匹配（`CATEGORY_RULES` 字典，约在脚本第 35-61 行）。正金额自动归类为 `收入`。未匹配项归入 `其他`。

## 数据流

```
PDF 文本提取（PyMuPDF）
  → 按格式解析（parse_transactions_format / parse_account_statement_format）
  → Trade Republic CSV 解析（parse_trade_republic_csv）
  → PayPal CSV 解析 + 银行流水匹配（parse_paypal_csv + match_paypal_to_bank）
  → detect_internal_transfers（内部转账/换汇检测）
  → post_process（清洗/去重/失败交易检测）
  → categorize（分类）
  → JSON 缓存（bank_transactions.json，含文件数量校验）
  → build_report() 按日期排序、按月/年聚合、预渲染 HTML/Plotly/JSON
  → bank_summary_2025.html（自包含四页签 SPA）
```

PayPal CSV 匹配逻辑：
- PayPal 真实交易（排除 Bank Deposit/Withdrawal/Authorization/Card Deposit）
- 第一轮：按 Gross 金额 + 日期（±5 天）匹配银行 PayPal SEPA 交易
- 匹配成功 → 用 PayPal 商户名替换银行 "PayPal Europe" 条目
- PayPal 收入匹配银行 PAYPAL 收入 → 标记银行为内部转账（提现）
- 第二轮（Balance 匹配）：Gross 未匹配的 Debit 交易，若 Balance ≠ Gross，
  用 Balance 金额匹配银行 PAYPAL 交易（部分余额支付场景）
  - 匹配成功 → 银行条目金额更新为 PayPal 全额，附注 Bankeinzug + Guthaben 明细
- 仍未匹配 → 作为新交易添加到数据中

缓存失效条件：`--force` 或 PDF 数量变化或任意 PDF 修改时间晚于缓存。

## GitHub

仓库：https://github.com/jeremyzejunchen/finance-tracker

只上传代码，不提交数据文件（PDF 银行流水、CSV 导出、缓存、生成的 HTML）。

## 数据逻辑联动规则

每次修改数据过滤逻辑（如新增排除规则、修改分类条件、调整内部转账/换汇/失败交易检测），
必须同步检查并更新以下**所有**组件：
- Python 端：`build_report()` 中 `ext_txns` 过滤条件、月度汇总循环、图表数据聚合
- JS 端：`updateReport()` 中的 `extFiltered`、`updateYearlyStats()` 的过滤条件
- JS 图表：`updateAllCharts()` → `updateYearlyTrendChart` / `updateMonthlyCategoryChart` / `updateMonthlyCompareChart` / `updateCumulativeNetChart`
- JS 饼图：`updatePieChart()` 调用时的数据参数
- 交易表：`applyFilters()` 的过滤逻辑

核心原则：**逻辑改一处，全局改所有**。不一致会导致图表/表格/KPI 显示互相矛盾的数据。

## 验证规则

每次代码改动完成后，必须用 Playwright 自动检查网页：
1. `python parse_bank_pdfs.py` 重新生成 HTML
2. 启动 `python -m http.server` 本地服务器
3. 用 Playwright 打开页面，确认零 JS 错误
4. 测试关键功能：筛选器联动、页签切换、分类展开、交易表过滤

## 工作约定

- `bank_summary_2025.html` 由 `parse_bank_pdfs.py` 生成。快速调试可以直接改 HTML，最终改动回归 Python 脚本。
- NAS 路径（`\\fritz.box\FRITZ.NAS\Jeremy_4T\记账`）是标准位置。
- `记账导出数据/` 中的 CSV 是 UTF-16LE 编码的鲨鱼记账 App 导出文件，不在当前流水线中使用。

## 当前状态（2026-07-11）

- **交易总数**：932 笔（含 48 笔内部转账，实际 884 笔有效交易）
- **总收入**：EUR 209,789.36 | **总支出**：EUR 161,876.41 | **净额**：EUR +47,912.95
- **PDF**：18 个文件（2025-01 ~ 2026-06）
- **CSV**：Trade Republic ME + WIFE 各一，PayPal ME (英文/有 Balance 字段) + WIFE (德文/无 Balance 字段)
- **最近改动**：PayPal Balance 匹配（部分余额支付场景），减少 3 笔重复

## PayPal CSV 数据结构

ME 账户 (Paypal-*-czj.CSV, 英文)：
- `Gross`：交易总额 | `Balance`：交易后 PayPal 余额
- 当 `Balance < 0`（借记交易后余额为负）→ 实际银行扣款 = `abs(Balance)`，其余来自 PayPal 余额
- 当 `Balance ≥ 0` → 全额来自 PayPal 余额，无银行扣款

WIFE 账户 (Paypal-*-cr.CSV, 德文)：无 Balance 列。
