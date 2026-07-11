#!/usr/bin/env python3
"""parse_bank_pdfs.py — 银行流水 PDF → 离线可视化 HTML 报告。

用法: 把 PDF 放入 银行流水/ 文件夹，然后运行:
    python parse_bank_pdfs.py
    python parse_bank_pdfs.py --force          # 强制重新解析
    python parse_bank_pdfs.py --month 2025-03  # 只看某月

输出: bank_summary_2025.html (自包含离线 HTML)
缓存: bank_transactions.json (避免重复解析)

依赖: PyMuPDF, plotly
备选: MinerU (magic-pdf) — 仅当 PyMuPDF 提取效果不佳时启用
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF

# ── 路径配置 ─────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
PDF_DIR = SCRIPT_DIR / "银行流水"
CACHE_FILE = SCRIPT_DIR / "bank_transactions.json"
OUTPUT_FILE = SCRIPT_DIR / "bank_summary_2025.html"

# ── 自有 IBAN（用于识别账户间内部转账）──────────────────────────────────────
OWN_IBANS = {
    "DE64290700240344376900",  # DB
    "DE79100123455797203011",  # MY-TR
    "DE08100123456340785111",  # WIFE-TR
}

# ── 分类规则 (从 记账.html 移植) ─────────────────────────────────────────────

CATEGORY_RULES = [
    ("购物", ["KAUFLAND", "LIDL", "ALDI", "REWE", "DM-DROGERIE", "GO ASIA",
              "ALIEXPRESS", "AMAZON", "EBAY", "TEGUT", "EDEKA", "NETTO",
              "MIX MARKT", "OBI", "PAYBACK PAY", "ROSSMANN", "优衣库", "宜家",
              "JELLYCAT", "TAOBAO", "S. DIGITS PAYMENT", "SUEDHANNOVER",
              "IKEA "]),
    ("交通", ["DEUTSCHE BAHN", "DB VERTRIEB", "PARKSTER", "TANKSTELLE",
              "TOTAL SERVICE", "CONTRIPARK", "PARKAUTOMATEN", "PARKEN"]),
    ("餐饮", ["UBER EATS", "LIEFERANDO", "KFC ", "MCDONALD", "BURGER KING",
              "UMG GASTRONOMIE", "GASTRONOMIE", "UBR. PENDING.UBER",
              "TANIA MOHAMED AHMED", "CAFE IM NEUEN", "LUTZ MICHAEL",
              "UBER ", "ROXX GOETTINGEN"]),
    ("居家", ["HARALD WINDEL", "STADTWERKE", "RUNDFUNK", "E.ON ENERGIE"]),
    ("汽车", ["VW LEASING", "KFZ", "ARAL", "SHELL", "HUK-COBURG",
              "VOLKSWAGEN AUTOVERS"]),
    ("宠物", ["FRESSNAPF", "ZOOPLUS", "TIERARZT", "TIERAERZTLICHES"]),
    ("通讯", ["VODAFONE", "TELEKOM", "O2 "]),
    ("服饰", ["LULULEMON", "ZALANDO", "PRIMARK", "UNIQLO"]),
    ("美容", ["DOUGLAS"]),
    ("娱乐", ["NETFLIX", "SPOTIFY", "FITNESS FUTURE", "FINION CAPITAL"]),
    ("旅行", ["BOOKING", "AIRBNB", "LUFTHANSA", "HOLIDAY INN", "CHECK24",
              "GOODMORNINGBERLIN", "PREUSS.SCHLOSSER"]),
    ("保险", ["HANSEMERKUR", "GOTHAER ALLGEMEINE", "SIGNAL IDUNA",
              "TECHNIKER KRANKENKASSE"]),
    ("医疗", ["APOTHEKE", "ARBEITER-SAMARITER", "KRANKENHAUS",
              "SANITATSHAUS", "DRK "]),
]

# ── PDF 格式检测 ────────────────────────────────────────────────────────────

def detect_format(text: str) -> str:
    if "Transactions persönliches Konto" in text:
        return "transactions"
    if "Account statement" in text or "Kontoauszug" in text:
        return "account_statement"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 格式一解析器: "Transactions_" (1-3月)
# ═══════════════════════════════════════════════════════════════════════════════

DATE_F1_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')
AMOUNT_F1_RE = re.compile(r'^[-+]?\d{1,3}(?:,\d{3})*\.\d{2}$')
SKIP_LINES_F1 = {
    '', 'EUR', 'Booking date', 'Value date', 'Transactions Payment details',
    'Debit', 'Credit', 'Currency', 'Booked transactions',
}


def parse_transactions_format(text: str) -> list[dict]:
    lines = text.split('\n')
    transactions = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        i += 1

        # 跳过空行和页眉页脚
        if not line or line in SKIP_LINES_F1:
            continue
        if any(x in line for x in ['https://', 'Zejun Chen', 'Customer number',
                                     'Created on', 'Sorted by', 'Old balance']):
            continue
        if line.startswith('Page ') and 'of' in line:
            continue

        # 匹配日期行 → 交易开始
        if not DATE_F1_RE.match(line):
            continue
        booking_date = line

        # 下一行：起息日
        if i >= len(lines):
            break
        value_date = lines[i].strip()
        if DATE_F1_RE.match(value_date):
            i += 1
        else:
            value_date = booking_date

        # 收集描述行，直到遇到金额行
        desc_lines = []
        amount_str = None
        while i < len(lines):
            nl = lines[i].strip()
            if not nl:
                i += 1
                continue
            if AMOUNT_F1_RE.match(nl):
                amount_str = nl
                i += 1
                break
            if DATE_F1_RE.match(nl) or nl in SKIP_LINES_F1:
                break
            if any(x in nl for x in ['https://', 'Customer number', 'Page ']):
                break
            desc_lines.append(nl)
            i += 1

        # 收集金额后的详情行（如 "Payment details KAUFLAND..." )
        detail_lines = []
        if amount_str:
            while i < len(lines):
                nl = lines[i].strip()
                if not nl:
                    i += 1
                    continue
                if nl == 'EUR':
                    i += 1
                    continue
                if DATE_F1_RE.match(nl) or nl in SKIP_LINES_F1:
                    break
                if any(x in nl for x in ['https://', 'Customer number', 'Page ',
                                          'Booked transactions', 'Sorted by']):
                    break
                detail_lines.append(nl)
                i += 1

        if amount_str and desc_lines:
            type_merchant = desc_lines[0]
            transactions.append({
                "booking_date": norm_date_f1(booking_date),
                "value_date": norm_date_f1(value_date),
                "amount": parse_amount_f1(amount_str),
                "type": extract_type_f1(type_merchant),
                "merchant": extract_merchant_f1(type_merchant, desc_lines[1:] + detail_lines),
                "details": "\n".join(desc_lines[1:] + detail_lines),
                "source_fmt": "f1",
            })

    return transactions


def extract_type_f1(line: str) -> str:
    for t in ['SEPA-Direct Debit', 'Debit Card Payment', 'SEPA Transfer',
              'Dauerauftrag', 'Gutschrift']:
        if t in line:
            return t
    return line.split('  ')[0].strip()


def extract_merchant_f1(first_line: str, rest: list[str]) -> str:
    """从第一行中去除交易类型前缀，得到商户名."""
    prefixes = ['SEPA-Direct Debit ', 'Debit Card Payment ',
                'SEPA Transfer ', 'Dauerauftrag ', 'Gutschrift ']
    for p in prefixes:
        if p in first_line:
            name = first_line.replace(p, '').strip()
            if name:
                return name
            break  # 等于纯 "Debit Card Payment"，商户名在 rest 中

    # first_line 就是纯类型 (如 "Debit Card Payment")，从详情行提取商户
    if rest:
        first_detail = rest[0]
        # 尝试 "Payment details STORE//CITY" 格式
        m = re.search(r'Payment details\s+(.+?)//', first_detail)
        if m:
            return m.group(1).strip()
        # 尝试直接的 "STORE//CITY" 格式
        m = re.match(r'^([^/]+?)//', first_detail)
        if m:
            return m.group(1).strip()
        return first_detail.strip()[:60]
    return first_line.strip()


def norm_date_f1(d: str) -> str:
    try:
        return datetime.strptime(d, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return d


def parse_amount_f1(s: str) -> float:
    return float(s.replace(',', ''))


# ═══════════════════════════════════════════════════════════════════════════════
# 格式二解析器: "Account_statement_" (5-6月)
# ═══════════════════════════════════════════════════════════════════════════════
# 特点: 日期被拆成 "DD-MM-" + "YYYY" 两行，金额在描述之前

AMOUNT_F2_RE = re.compile(r'^[+-]\s*\d{1,3}(?:[.,]\d{3})*[.,]\d{2}$')
DATE_PART_F2_RE = re.compile(r'^\d{2}-\d{2}-$')
YEAR_F2_RE = re.compile(r'^\d{4}$')

# 页眉/页脚行，需跳过
SKIP_F2_LINES = {
    '', 'Credit', 'Debit', 'Item', 'Value', 'Booking', 'date', 'EUR',
    'IBAN', 'of', 'Page', 'Statement', 'DE64 2907 0024 0344 3769 00',
    'Deutsche Bank AG', 'Filiale', 'Göttingen', 'Mr.', 'Zejun Chen',
    'Zindelstraße 3-5', '37073 Göttingen', 'Beratungsteam',
    'Ulmenweg 2B', '37077 Göttingen',
}
SKIP_F2_PREFIXES = (
    '0000000003', 'Telephone', '24-hour', 'May ', 'June ', 'July ',
    'August ', 'September ', 'October ', 'November ', 'December ',
    'January ', 'February ', 'March ', 'April ',
    'Account statement', 'Account holder', 'Previous balance',
)


def parse_account_statement_format(text: str) -> list[dict]:
    lines = text.split('\n')
    transactions = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        i += 1

        # 跳过空白和页眉页脚
        if line in SKIP_F2_LINES:
            continue
        if not line or any(line.startswith(x) for x in SKIP_F2_PREFIXES):
            # "Previous balance" 行后紧跟的余额金额也要跳过
            if 'Previous balance' in line:
                while i < len(lines) and not lines[i].strip():
                    i += 1
                if i < len(lines) and AMOUNT_F2_RE.match(lines[i].strip()):
                    i += 1  # 跳过余额金额
            continue

        # 匹配金额行 (+/- 开头)
        m = AMOUNT_F2_RE.match(line)
        if not m:
            continue
        amount_raw = line

        # 下一非空行 → 交易类型
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        type_line = lines[i].strip()
        i += 1

        # 收集商户名行，直到遇到拆分日期或下一笔金额
        merchant_lines = []
        while i < len(lines):
            nl = lines[i].strip()
            if not nl:
                i += 1
                continue
            if DATE_PART_F2_RE.match(nl):
                break
            if AMOUNT_F2_RE.match(nl):
                break
            merchant_lines.append(nl)
            i += 1

        # 读取两个拆分日期: DD-MM- + YYYY (起息日), DD-MM- + YYYY (记账日)
        def read_split_date():
            nonlocal i
            if i < len(lines) and DATE_PART_F2_RE.match(lines[i].strip()):
                d_part = lines[i].strip()
                i += 1
                if i < len(lines) and YEAR_F2_RE.match(lines[i].strip()):
                    full = d_part + lines[i].strip()
                    i += 1
                    return full
            return None

        value_date = read_split_date()
        booking_date = read_split_date()

        # 收集详情行，直到下一笔金额或日期
        details_lines = []
        while i < len(lines):
            nl = lines[i].strip()
            if not nl:
                i += 1
                continue
            if AMOUNT_F2_RE.match(nl) or DATE_PART_F2_RE.match(nl):
                break
            details_lines.append(nl)
            i += 1

        if booking_date:
            merchant = extract_merchant_f2(type_line, merchant_lines)
            transactions.append({
                "booking_date": norm_date_f2(booking_date),
                "value_date": norm_date_f2(value_date or booking_date),
                "amount": parse_amount_f2(amount_raw),
                "type": norm_type_f2(type_line),
                "merchant": merchant,
                "details": "\n".join(details_lines),
                "source_fmt": "f2",
            })

    return transactions


# ═══════════════════════════════════════════════════════════════════════════════
# CSV 解析器: Trade Republic 导出
# ═══════════════════════════════════════════════════════════════════════════════

def parse_trade_republic_csv(csv_path: Path) -> list[dict]:
    """解析 Trade Republic CSV → 统一交易格式。只提取 CASH 交易。"""
    import csv as _csv
    txns = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = _csv.DictReader(f)
        for row in reader:
            cat = row.get('category', '')
            if cat != 'CASH':
                continue  # 跳过 TRADING（投资交易）

            amt_str = row.get('amount', '0').strip()
            try:
                amount = float(amt_str)
            except ValueError:
                continue

            booking_date = row.get('date', '')[:10]  # YYYY-MM-DD
            merchant = row.get('name', '').strip()
            description = row.get('description', '').strip()
            txn_type = row.get('type', '').strip()
            cparty_iban = row.get('counterparty_iban', '').strip()

            # 检测内部转账
            is_internal = (
                'TRANSFER' in txn_type and
                cparty_iban in OWN_IBANS
            )

            txns.append({
                "booking_date": booking_date,
                "value_date": booking_date,
                "amount": amount,
                "type": txn_type,
                "merchant": merchant,
                "details": description,
                "source_fmt": "tr_csv",
                "account": "TR",
                "is_internal_transfer": is_internal,
            })
    return txns


def detect_internal_transfers(all_txns: list[dict]) -> None:
    """标记 PDF 侧的内部转账（通过 IBAN 匹配）。"""
    # 从已有交易的 details 中提取 IBAN
    iban_re = re.compile(r'DE\d{20}')
    for t in all_txns:
        if t.get('is_internal_transfer'):
            continue
        # 检查 details 中的 IBAN
        details = t.get('details', '')
        ibans = iban_re.findall(details)
        if any(iban in OWN_IBANS for iban in ibans):
            t['is_internal_transfer'] = True


def extract_merchant_f2(type_line: str, merchant_lines: list[str]) -> str:
    """从类型行和后续行提取商户名."""
    prefixes = [
        'SEPA Lastschrifteinzug von ',
        'SEPA Überweisung an ',
        'SEPA Überweisung von ',
        'SEPA Echtzeitüberweisung an ',
        'SEPA Echtzeitüberweisung von ',
        'Echtzeitüberweisung an ',
        'Echtzeitüberweisung von ',
        'Dauerauftrag an ',
        'Gutschrift von ',
    ]
    for p in prefixes:
        if type_line.startswith(p):
            name = type_line[len(p):].strip()
            if name:
                return name
            break  # prefix matched but name empty → check merchant_lines

    # Kartenzahlung 或其他：商户名在后续行
    if merchant_lines:
        return merchant_lines[0].strip()
    return type_line.strip()


def norm_type_f2(line: str) -> str:
    if 'Lastschrifteinzug' in line:
        return 'SEPA-Direct Debit'
    if 'Überweisung an' in line:
        return 'SEPA Transfer (out)'
    if 'Überweisung von' in line:
        return 'SEPA Transfer (in)'
    if 'Kartenzahlung' in line:
        return 'Debit Card Payment'
    if 'Gutschrift' in line:
        return 'Credit'
    if 'Dauerauftrag' in line:
        return 'Standing Order'
    return line


def norm_date_f2(d: str) -> str:
    try:
        return datetime.strptime(d, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return d


def parse_amount_f2(s: str) -> float:
    """'- 19.90' 或 '+ 3.10' 或 '- 1.024,54' 或 '- 3,967.42' → float."""
    s = s.strip()
    sign = -1 if s.startswith('-') else 1
    s = s[1:].strip()  # 去掉符号
    if ',' in s and '.' in s:
        # 倒数第3位是逗号 → 德式 (1.024,54)
        if len(s) > 3 and s[-3] == ',':
            s = s.replace('.', '').replace(',', '.')
        # 倒数第3位是点 → 英美式 (1,024.54)
        else:
            s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
    return sign * float(s)


# ── 分类 ─────────────────────────────────────────────────────────────────────

def categorize(merchant: str, amount: float, details: str = "") -> str:
    upper = merchant.upper()
    if amount > 0:
        return "收入"
    for cat, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in upper:
                return cat
    # PayPal: 从详情中推断真实商户
    if 'PAYPAL' in upper:
        du = details.upper()
        if 'ALIEXPRESS' in du or 'AMAZON' in du or 'EBAY' in du or 'EINKAUF BEI' in du:
            return "购物"
        if 'UBER' in du:
            return "餐饮"
    return "其他"


# ═══════════════════════════════════════════════════════════════════════════════
# 缓存
# ═══════════════════════════════════════════════════════════════════════════════

def load_cache(force: bool = False) -> dict:
    if force or not CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        cache = raw.get("transactions", raw)  # 兼容新旧格式
        cache_mtime = CACHE_FILE.stat().st_mtime
        pdf_files = list(PDF_DIR.glob("*.pdf"))
        cache_count = raw.get("_count", 0)
        if len(pdf_files) != cache_count:
            return []
        for pdf in pdf_files:
            if pdf.stat().st_mtime > cache_mtime:
                return []  # 有新 PDF 或被修改，缓存失效
        return cache
    except (json.JSONDecodeError, KeyError):
        return []


def save_cache(transactions: list[dict]):
    pdf_count = len(list(PDF_DIR.glob("*.pdf")))
    CACHE_FILE.write_text(
        json.dumps({"_count": pdf_count, "transactions": transactions}, ensure_ascii=False, indent=2),
        encoding='utf-8')


# ═══════════════════════════════════════════════════════════════════════════════
# HTML 报告生成
# ═══════════════════════════════════════════════════════════════════════════════

def build_report(transactions: list[dict]) -> str:
    import re
    import plotly.graph_objects as go
    from plotly.io import to_html

    if not transactions:
        return "<html><body><h1>未找到交易数据</h1></body></html>"

    txns = sorted(transactions, key=lambda t: t['booking_date'])

    # ── 月度汇总 ──
    month_keys = sorted(set(t['booking_date'][:7] for t in txns))
    cat_month = defaultdict(lambda: defaultdict(float))
    month_income = defaultdict(float)
    month_expense = defaultdict(float)
    all_cats = set()

    for t in txns:
        mk = t['booking_date'][:7]
        cat = t.get('category', '其他')
        if t['amount'] > 0:
            month_income[mk] += t['amount']
        else:
            month_expense[mk] += abs(t['amount'])
            cat_month[mk][cat] += abs(t['amount'])
            all_cats.add(cat)

    # 图表1: 月度支出分类 (堆叠柱状图)
    sorted_cats = sorted(all_cats, key=lambda c: sum(cat_month[m].get(c, 0) for m in month_keys), reverse=True)
    fig_cat = go.Figure()
    for cat in sorted_cats:
        vals = [cat_month[m].get(cat, 0) for m in month_keys]
        if any(v > 0 for v in vals):
            fig_cat.add_trace(go.Bar(name=cat, x=month_keys, y=vals))
    fig_cat.update_layout(
        title="月度支出分类", barmode='stack',
        xaxis_title="月份", yaxis_title="EUR",
        template='plotly_white', height=480, margin=dict(l=40, r=20, t=50, b=40),
    )

    # 图表2: 月度收支对比
    fig_month = go.Figure()
    fig_month.add_trace(go.Bar(name='收入', x=month_keys,
        y=[month_income[m] for m in month_keys], marker_color='#10b981'))
    fig_month.add_trace(go.Bar(name='支出', x=month_keys,
        y=[month_expense[m] for m in month_keys], marker_color='#ef4444'))
    fig_month.update_layout(
        title="月度收支对比", barmode='group',
        xaxis_title="月份", yaxis_title="EUR",
        template='plotly_white', height=400, margin=dict(l=40, r=20, t=50, b=40),
    )

    # 图表3: 累计净额
    running = []
    s = 0.0
    for t in txns:
        s += t['amount']
        running.append(s)
    fig_cum = go.Figure()
    fig_cum.add_trace(go.Scatter(
        x=[t['booking_date'] for t in txns], y=running,
        mode='lines', fill='tozeroy', name='累计净额',
        line=dict(color='#6366f1', width=2),
    ))
    fig_cum.add_hline(y=0, line_dash="dash", line_color="#94a3b8")
    fig_cum.update_layout(
        title="累计净额走势", xaxis_title="日期", yaxis_title="EUR",
        template='plotly_white', height=360, margin=dict(l=40, r=20, t=50, b=40),
    )

    # 图表4: 分类占比环形图
    cat_totals = {}
    for cat in sorted_cats:
        total = sum(cat_month[m].get(cat, 0) for m in month_keys)
        if total > 0:
            cat_totals[cat] = total
    fig_pie = go.Figure()
    fig_pie.add_trace(go.Pie(
        labels=list(cat_totals.keys()), values=list(cat_totals.values()),
        hole=0.45, textinfo='percent', textfont=dict(size=11),
        textposition='outside', automargin=True,
    ))
    fig_pie.update_layout(
        title="支出分类占比", template='plotly_white',
        height=480, margin=dict(l=40, r=80, t=50, b=20),
        legend=dict(orientation='v', y=0.5, x=1.05, xanchor='left'),
        showlegend=True,
    )

    # 图表5: 收入分类占比
    income_cats = defaultdict(float)
    for t in txns:
        if t['amount'] > 0:
            income_cats[t.get('category', '收入')] += t['amount']
    fig_income_pie = go.Figure()
    if income_cats:
        fig_income_pie.add_trace(go.Pie(
            labels=list(income_cats.keys()), values=list(income_cats.values()),
            hole=0.45, textinfo='percent', textfont=dict(size=11),
            textposition='outside', automargin=True,
        ))
        fig_income_pie.update_layout(
            title="收入分类占比", template='plotly_white',
            height=480, margin=dict(l=40, r=80, t=50, b=20),
            legend=dict(orientation='v', y=0.5, x=1.05, xanchor='left'),
        )

    # 图表6: 年度收支趋势（每年一张，按年份筛选时切换显示）
    years = sorted(set(m[:4] for m in month_keys))
    yearly_charts = {}
    for yr in years:
        yr_months = [m for m in month_keys if m.startswith(yr)]
        yr_labels = [m[-2:] + '月' for m in yr_months]  # "01月", "02月"...
        yr_inc = [month_income[m] for m in yr_months]
        yr_exp = [month_expense[m] for m in yr_months]
        yr_bal = [month_income[m] - month_expense[m] for m in yr_months]
        fig = go.Figure()
        fig.add_trace(go.Bar(name='收入', x=yr_labels, y=yr_inc, marker_color='#10b981'))
        fig.add_trace(go.Bar(name='支出', x=yr_labels, y=yr_exp, marker_color='#ef4444'))
        fig.add_trace(go.Scatter(name='结余', x=yr_labels, y=yr_bal,
            mode='lines+markers', line=dict(color='#6366f1', width=3), marker=dict(size=8)))
        fig.update_layout(
            title=f"{yr} 年度收支趋势", barmode='group',
            xaxis_title="月份", yaxis_title="EUR",
            template='plotly_white', height=400, margin=dict(l=40, r=20, t=50, b=40),
            hovermode='x unified',
        )
        yearly_charts[yr] = fig

    # ── JSON 数据嵌入（供前端 JS 筛选使用）──
    import json as _json
    txns_json = _json.dumps(txns, ensure_ascii=False, default=str)

    # ── 统计卡片（排除内部转账）──
    ext_txns = [t for t in txns if not t.get('is_internal_transfer')]
    total_in = sum(t['amount'] for t in ext_txns if t['amount'] > 0)
    total_out = abs(sum(t['amount'] for t in ext_txns if t['amount'] < 0))
    net = total_in - total_out
    date_range = f"{txns[0]['booking_date']} ~ {txns[-1]['booking_date']}"
    avg_monthly = total_out / len(month_keys) if month_keys else 0
    total_count = len(txns)

    # ── 交易明细表行 ──
    table_rows = []
    accounts = sorted(set(t.get('account', 'DB') for t in txns))
    for t in reversed(txns):
        css = 'inc' if t['amount'] > 0 else 'exp'
        if t.get('is_internal_transfer'):
            css += ' internal'
        cat = t.get('category', '其他')
        acct = t.get('account', 'DB')
        table_rows.append(
            f'<tr class="{css}" data-category="{cat}" data-account="{acct}" data-internal="{1 if t.get("is_internal_transfer") else 0}">'
            f'<td>{t["booking_date"]}</td>'
            f'<td>{t.get("merchant","")[:55]}</td>'
            f'<td><span class="tag">{cat}</span></td>'
            f'<td class="amt" data-amount="{t["amount"]}">{t["amount"]:+,.2f}</td>'
            f'</tr>'
        )

    cat_counts = defaultdict(int)
    for t in txns:
        cat_counts[t.get('category', '其他')] += 1
    top_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)

    # 提取 Plotly.js 库（取最大的 script，即 4.8MB 的库代码，排除空图表的渲染调用）
    empty_fig = go.Figure()
    plotly_full = to_html(empty_fig, include_plotlyjs=True, full_html=False)
    scripts = re.findall(r'(<script[^>]*>.*?</script>)', plotly_full, re.DOTALL)
    plotly_js = max(scripts, key=len)  # 库脚本总是最大的

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>银行账单总结 {date_range}</title>
{plotly_js}
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.2.1/css/all.min.css">
<style>
/* ═══════════════════════════════════════════════════════════════════════════
   Base & Reset
   ═══════════════════════════════════════════════════════════════════════════ */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f0f2f5;--surface:#fff;--text:#1e293b;--text2:#64748b;
  --border:#e8ecf1;--accent:#4f46e5;--accent2:#818cf8;
  --green:#10b981;--red:#ef4444;--net:#6366f1;
  --kpi-shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --card-shadow:0 1px 3px rgba(0,0,0,.06);
  --radius:12px;--radius-sm:8px;
}}
body{{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif;
  background:var(--bg);color:var(--text);padding:24px 16px;line-height:1.5;
  -webkit-font-smoothing:antialiased;
}}
main{{max-width:1280px;margin:0 auto}}

/* ═══════════════════════════════════════════════════════════════════════════
   Header
   ═══════════════════════════════════════════════════════════════════════════ */
.header{{text-align:center;margin-bottom:28px}}
.header h1{{font-size:1.5rem;font-weight:700;letter-spacing:-.3px}}
.header .sub{{color:var(--text2);font-size:.85rem;margin-top:4px}}

/* ── Tab Navigation ── */
.tabs{{
  display:flex;border-bottom:2px solid var(--border);margin-bottom:24px;gap:0;
}}
.tabs button{{
  padding:10px 22px;border:none;background:none;color:var(--text2);
  font-size:.9rem;font-weight:500;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-2px;transition:color .15s,border-color .15s;font-family:inherit;
}}
.tabs button:hover{{color:var(--text)}}
.tabs button.active{{color:var(--accent);border-bottom-color:var(--accent)}}
.tab-content{{display:none}}
.tab-content.active-tab{{display:block}}

/* ── Filter Bar ── */
.filter-grid{{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:14px;
}}
.filter-item label{{
  display:block;font-size:.78rem;font-weight:600;color:var(--text2);
  margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;
}}
.filter-item select,.filter-item input{{
  width:100%;padding:8px 12px;border:1px solid var(--border);
  border-radius:var(--radius-sm);font-size:.88rem;font-family:inherit;
  background:var(--surface);color:var(--text);outline:none;
  transition:border-color .15s;
}}
.filter-item select:focus,.filter-item input:focus{{
  border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.1);
}}

/* ── Detail Table ── */
.detail-table{{width:100%;border-collapse:collapse;font-size:.86rem}}
.detail-table th{{
  background:var(--bg);color:var(--text2);padding:10px 12px;text-align:left;
  font-weight:600;font-size:.78rem;border-bottom:2px solid var(--border);
}}
.detail-table td{{padding:8px 12px;border-bottom:1px solid var(--border)}}
.detail-table tbody tr:hover td{{background:#f8fafc}}

/* ── Notification ── */
.notification{{
  position:fixed;top:20px;right:20px;background:var(--green);color:#fff;
  padding:12px 20px;border-radius:var(--radius-sm);font-size:.88rem;
  box-shadow:0 4px 12px rgba(0,0,0,.15);z-index:1000;transition:opacity .3s;
}}
.notification.hidden{{opacity:0;pointer-events:none}}

/* ── Settings ── */
.cat-list{{display:flex;flex-direction:column;gap:4px}}
.cat-item{{
  display:flex;justify-content:space-between;align-items:center;
  padding:6px 10px;background:var(--bg);border-radius:6px;font-size:.85rem;
}}
.cat-item button{{
  background:none;border:none;cursor:pointer;color:var(--text2);padding:2px 6px;
  font-size:.8rem;border-radius:4px;transition:color .15s;
}}
.cat-item button:hover{{color:var(--red)}}
.cat-item .edit-btn:hover{{color:var(--accent)}}
.cat-item input{{
  padding:2px 6px;border:1px solid var(--accent);border-radius:4px;
  font-size:.85rem;font-family:inherit;width:100%;
}}
.tag-chip{{
  display:inline-flex;align-items:center;gap:4px;
  padding:3px 10px;background:var(--bg);border-radius:12px;font-size:.8rem;
  cursor:pointer;transition:background .15s;
}}
.tag-chip:hover{{background:var(--border)}}
.tag-chip .rm{{color:var(--text2);font-size:.7rem}}
.tag-chip .rm:hover{{color:var(--red)}}

/* ── Confirm Dialog ── */
.dialog-overlay{{
  position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
  z-index:2000;background:rgba(0,0,0,.4);
}}
.dialog-overlay.hidden{{display:none}}
.dialog-box{{
  background:var(--surface);border-radius:var(--radius);padding:24px;
  max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.2);
}}
.dialog-box h3{{margin-bottom:8px;font-size:1.1rem}}
.dialog-box p{{color:var(--text2);font-size:.88rem;margin-bottom:20px}}
.dialog-box .btns{{display:flex;justify-content:flex-end;gap:8px}}
.dialog-box .btns button{{
  padding:8px 18px;border:none;border-radius:6px;cursor:pointer;font-size:.85rem;font-family:inherit;
}}
.dialog-box .btn-cancel{{background:var(--bg);color:var(--text)}}
.dialog-box .btn-ok{{background:var(--red);color:#fff}}

/* ═══════════════════════════════════════════════════════════════════════════
   KPI Cards
   ═══════════════════════════════════════════════════════════════════════════ */
.kpi{{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
  gap:14px;margin-bottom:24px;
}}
.kpi>div{{
  background:var(--surface);border-radius:var(--radius);
  padding:20px 22px;box-shadow:var(--kpi-shadow);
  border-left:4px solid transparent;transition:transform .15s,box-shadow .15s;
}}
.kpi>div:hover{{transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.08)}}
.kpi>div.kpi-in{{border-left-color:var(--green)}}
.kpi>div.kpi-out{{border-left-color:var(--red)}}
.kpi>div.kpi-net{{border-left-color:var(--net)}}
.kpi>div.kpi-count{{border-left-color:var(--accent)}}
.kpi .lbl{{font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;font-weight:500}}
.kpi .val{{font-size:1.6rem;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}}
.kpi .val.in{{color:var(--green)}}.kpi .val.out{{color:var(--red)}}.kpi .val.net{{color:var(--net)}}
.kpi .sub-val{{font-size:.75rem;color:var(--text2);margin-top:2px}}

/* ═══════════════════════════════════════════════════════════════════════════
   Cards & Charts
   ═══════════════════════════════════════════════════════════════════════════ */
.card{{
  background:var(--surface);border-radius:var(--radius);
  padding:20px 22px;box-shadow:var(--card-shadow);margin-bottom:18px;
}}
.card h2{{font-size:1rem;font-weight:600;margin-bottom:14px;color:var(--text);display:flex;align-items:center;gap:8px}}
.card h2::before{{content:'';display:inline-block;width:4px;height:18px;background:var(--accent);border-radius:2px}}

.charts-grid{{
  display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px;
}}
.charts-grid .card{{margin-bottom:0}}
@media(max-width:860px){{.charts-grid{{grid-template-columns:1fr}}}}

/* ═══════════════════════════════════════════════════════════════════════════
   Category Tags (interactive pills)
   ═══════════════════════════════════════════════════════════════════════════ */
.top-cats{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;padding:0 22px}}
.top-cats button{{
  padding:4px 14px;border-radius:20px;font-size:.78rem;font-weight:500;
  border:1px solid var(--border);background:var(--surface);color:var(--text2);
  cursor:pointer;transition:all .15s;font-family:inherit;
}}
.top-cats button:hover{{background:#eef2ff;color:var(--accent);border-color:var(--accent2)}}
.top-cats button.active{{background:var(--accent);color:#fff;border-color:var(--accent)}}

/* ═══════════════════════════════════════════════════════════════════════════
   Table
   ═══════════════════════════════════════════════════════════════════════════ */
.tbl-head{{
  display:flex;justify-content:space-between;align-items:center;
  padding:0 22px 14px;gap:12px;flex-wrap:wrap;
}}
.search-box{{
  position:relative;flex:1;max-width:320px;
}}
.search-box input{{
  width:100%;padding:8px 14px 8px 36px;border:1px solid var(--border);
  border-radius:var(--radius-sm);font-size:.88rem;outline:none;
  background:var(--bg);color:var(--text);font-family:inherit;
  transition:border-color .15s,box-shadow .15s;
}}
.search-box input:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.1)}}
.search-box::before{{
  content:'\\1F50D';position:absolute;left:10px;top:50%;transform:translateY(-50%);
  font-size:.85rem;opacity:.5;pointer-events:none;
}}
.search-clear{{display:none;position:absolute;right:8px;top:50%;transform:translateY(-50%);
  background:none;border:none;cursor:pointer;font-size:1rem;color:var(--text2);padding:2px 6px;border-radius:50%}}
.search-clear:hover{{color:var(--text)}}
.search-clear.visible{{display:block}}

.tbl-count{{font-size:.82rem;color:var(--text2);white-space:nowrap}}

table{{width:100%;border-collapse:collapse;font-size:.86rem}}
thead th{{
  background:var(--bg);color:var(--text2);padding:10px 12px;text-align:left;
  font-weight:600;font-size:.78rem;position:sticky;top:0;z-index:1;
  cursor:pointer;user-select:none;white-space:nowrap;transition:color .15s;
}}
thead th:hover{{color:var(--text)}}
thead th.sorted{{color:var(--accent)}}
thead th .sort-arrow{{font-size:.7rem;margin-left:3px;opacity:.4}}
thead th.sorted .sort-arrow{{opacity:1}}
tbody td{{padding:8px 12px;border-bottom:1px solid var(--border)}}
tr.hidden{{display:none}}
tr.internal td{{color:var(--text2);font-style:italic}}
tr:hover td{{background:#f8fafc}}
.amt{{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}}
tr.inc .amt{{color:var(--green)}}tr.exp .amt{{color:var(--red)}}
.tag{{
  display:inline-block;padding:2px 10px;border-radius:12px;font-size:.76rem;
  background:#f1f5f9;color:var(--text2);font-weight:500;
}}

.tbl-wrap{{max-height:600px;overflow-y:auto;border-radius:0 0 var(--radius) var(--radius)}}

/* ═══════════════════════════════════════════════════════════════════════════
   Dark Mode
   ═══════════════════════════════════════════════════════════════════════════ */
[data-theme="dark"] {{
  --bg:#0b0f19;--surface:#111827;--text:#e2e8f0;--text2:#94a3b8;
  --border:#1e293b;--accent:#818cf8;--accent2:#6366f1;
}}
[data-theme="dark"] tr:hover td{{background:#1a2332}}
[data-theme="dark"] .tag{{background:#1e293b;color:var(--text2)}}
[data-theme="dark"] thead th{{background:#1a2232}}
[data-theme="dark"] .top-cats button{{background:var(--surface);border-color:#374151}}
[data-theme="dark"] .top-cats button:hover{{background:#1e1b4b;border-color:var(--accent)}}
[data-theme="dark"] .search-box input{{background:#1a2232}}
[data-theme="dark"] .cat-item{{background:#1a2232}}
[data-theme="dark"] .tag-chip{{background:#1a2232}}
[data-theme="dark"] .detail-table th{{background:#1a2232}}
[data-theme="dark"] .detail-table tbody tr:hover td{{background:#1a2332}}
[data-theme="dark"] .filter-item select,[data-theme="dark"] .filter-item input{{background:#1a2232;border-color:#374151}}
[data-theme="dark"] .dialog-box{{background:#1e293b;border:1px solid #374151}}
[data-theme="dark"] .dialog-box .btn-cancel{{background:#0b0f19;color:#e2e8f0}}

/* ── Theme Toggle ── */
.theme-toggle{{
  position:fixed;top:16px;right:16px;z-index:100;
  width:40px;height:40px;border-radius:50%;border:1px solid var(--border);
  background:var(--surface);color:var(--text);cursor:pointer;
  font-size:1.1rem;display:flex;align-items:center;justify-content:center;
  box-shadow:var(--kpi-shadow);transition:transform .15s;
}}
.theme-toggle:hover{{transform:scale(1.1)}}
</style>
</head>
<body>
<button class="theme-toggle" id="themeToggle" title="切换明暗模式">&#9789;</button>
<main>

<div class="header">
<h1>银行账单总结</h1>
<p class="sub">{date_range} · 共 {total_count} 笔交易 · 月均支出 €{avg_monthly:,.2f}</p>
</div>

<nav class="tabs">
<button class="active" data-tab="tab-report">报表</button>
<button data-tab="tab-charts">图表</button>
<button data-tab="tab-settings">设置</button>
</nav>

<div id="tab-report" class="tab-content active-tab">

<!-- 筛选栏 -->
<div class="card" style="margin-bottom:20px">
<h2 style="margin-bottom:16px">数据筛选</h2>
<div class="filter-grid">
<div class="filter-item">
<label>账户</label>
<select id="report-account"><option value="all">全部账户</option>
{"".join(f'<option value="{a}">{a}</option>' for a in accounts)}
</select>
</div>
<div class="filter-item">
<label>年份</label>
<select id="report-year"><option value="all">全部年份</option>
{"".join(f'<option value="{y}" {"selected" if y == years[-1] else ""}>{y}</option>' for y in years)}
</select>
</div>
<div class="filter-item">
<label>月份</label>
<select id="report-month"><option value="all">全部月份</option>
{"".join(f'<option value="{m}">{m}</option>' for m in month_keys)}
</select>
</div>
<div class="filter-item">
<label>分类</label>
<select id="report-category"><option value="all">所有分类</option>
{"".join(f'<option value="{c}">{c}</option>' for c in sorted_cats)}
</select>
</div>
<div class="filter-item">
<label>描述搜索</label>
<input type="text" id="report-search" placeholder="搜索商户…" autocomplete="off">
</div>
<div class="filter-item">
<label>金额范围</label>
<div style="display:flex;gap:8px">
<input type="number" id="report-amt-min" placeholder="最小" step="0.01" style="flex:1">
<input type="number" id="report-amt-max" placeholder="最大" step="0.01" style="flex:1">
</div>
</div>
</div>
</div>

<!-- 月度 KPI -->
<div class="kpi" id="report-kpi">
<div class="kpi-in"><div class="lbl">总收入</div><div class="val in" id="rpt-income">€{total_in:,.2f}</div></div>
<div class="kpi-out"><div class="lbl">总支出</div><div class="val out" id="rpt-expense">€{total_out:,.2f}</div></div>
<div class="kpi-net"><div class="lbl">净额</div><div class="val net" id="rpt-balance">€{net:+,.2f}</div></div>
<div class="kpi-count"><div class="lbl">交易笔数</div><div class="val" id="rpt-count">{len(txns)}</div></div>
</div>

<!-- 饼图（JS 动态渲染，跟随筛选联动）-->
<div class="charts-grid">
<div class="card"><h2>支出分类占比</h2><div id="rpt-expense-pie" style="height:440px"></div></div>
<div class="card"><h2>收入分类占比</h2><div id="rpt-income-pie" style="height:440px"></div></div>
</div>

<!-- 分类明细表 -->
<div class="card">
<h2>分类明细</h2>
<div style="overflow-x:auto"><table class="detail-table">
<thead><tr>
<th>分类</th><th style="text-align:right">金额</th><th style="text-align:right">占比</th><th style="text-align:right">笔数</th>
</tr></thead>
<tbody id="rpt-expense-detail"></tbody>
</table></div>
</div>

<!-- 年度趋势图（每年一张） -->
<div class="card" style="margin-top:20px"><h2>年度收支趋势</h2>
{"".join(f'<div class="yearly-chart" data-year="{y}" style="display:none">{to_html(fig, include_plotlyjs=False, full_html=False)}</div>' for y, fig in yearly_charts.items())}
</div>

<!-- 年度统计 -->
<div class="card" style="margin-top:20px">
<h2>年度汇总统计</h2>
<div class="kpi" id="yearly-kpi">
<div class="kpi-in"><div class="lbl">年度总收入</div><div class="val in" id="yr-income">€{total_in:,.2f}</div></div>
<div class="kpi-out"><div class="lbl">年度总支出</div><div class="val out" id="yr-expense">€{total_out:,.2f}</div></div>
<div class="kpi-net"><div class="lbl">年度净额</div><div class="val net" id="yr-balance">€{net:+,.2f}</div></div>
</div>

<div class="charts-grid" style="margin-top:16px">
<div>
<h3 style="font-size:.9rem;margin-bottom:12px;color:var(--text2)">支出分类占比</h3>
<table class="detail-table"><thead><tr>
<th>分类</th><th style="text-align:right">金额</th><th style="text-align:right">占比</th>
</tr></thead><tbody id="yr-exp-detail"></tbody></table>
</div>
<div>
<h3 style="font-size:.9rem;margin-bottom:12px;color:var(--text2)">收入分类占比</h3>
<table class="detail-table"><thead><tr>
<th>分类</th><th style="text-align:right">金额</th><th style="text-align:right">占比</th>
</tr></thead><tbody id="yr-inc-detail"></tbody></table>
</div>
</div>

<!-- 月度对比表 -->
<h3 style="font-size:.9rem;margin:20px 0 12px;color:var(--text2)">月度收支对比</h3>
<table class="detail-table"><thead><tr>
<th>月份</th><th style="text-align:right">收入</th><th style="text-align:right">支出</th><th style="text-align:right">结余</th>
</tr></thead><tbody id="yr-monthly-detail"></tbody></table>
</div>

<!-- 交易表 -->
<div class="card" style="padding:0;overflow:hidden;margin-top:20px">
<div class="tbl-head">
<h2 style="padding-left:22px;margin-bottom:0">交易明细</h2>
<div class="search-box">
<input type="text" id="searchInput" placeholder="搜索商户 / 分类…" autocomplete="off">
<button class="search-clear" id="searchClear" title="清除">&times;</button>
</div>
<span class="tbl-count" id="tblCount">{total_count} / {total_count} 条记录</span>
</div>
<div class="top-cats" id="catFilters">
<button class="active" data-cat="">全部分类 ({total_count})</button>
{"".join(f'<button data-cat="{c}">{c} ({n})</button>' for c,n in top_cats)}
</div>
<div class="tbl-wrap"><table>
<thead><tr>
<th data-col="0">日期 <span class="sort-arrow">↑↓</span></th>
<th data-col="1">商户 <span class="sort-arrow">↑↓</span></th>
<th data-col="2">分类 <span class="sort-arrow">↑↓</span></th>
<th data-col="3" style="text-align:right">金额 <span class="sort-arrow">↑↓</span></th>
</tr></thead>
<tbody>{"".join(table_rows)}</tbody>
</table></div>
</div>

</div><!-- /tab-report -->

<div id="tab-charts" class="tab-content">

<div class="card"><h2>月度支出分类</h2>{to_html(fig_cat, include_plotlyjs=False, full_html=False)}</div>

<div class="charts-grid">
<div class="card"><h2>月度收支对比</h2>{to_html(fig_month, include_plotlyjs=False, full_html=False)}</div>
<div class="card"><h2>累计净额走势</h2>{to_html(fig_cum, include_plotlyjs=False, full_html=False)}</div>
</div>

</div><!-- /tab-charts -->

<div id="tab-settings" class="tab-content">

<div class="charts-grid">
<!-- 分类管理 -->
<div class="card">
<h2>分类管理</h2>
<div style="margin-bottom:16px">
<h3 style="font-size:.85rem;color:var(--text2);margin-bottom:8px">支出分类</h3>
<div id="bank-expense-cats" class="cat-list"></div>
<div style="display:flex;gap:8px;margin-top:8px">
<input type="text" id="new-expense-cat" placeholder="添加支出分类" style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:.85rem">
<button id="add-expense-cat" style="padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.85rem">添加</button>
</div>
</div>
<div>
<h3 style="font-size:.85rem;color:var(--text2);margin-bottom:8px">收入分类</h3>
<div id="bank-income-cats" class="cat-list"></div>
<div style="display:flex;gap:8px;margin-top:8px">
<input type="text" id="new-income-cat" placeholder="添加收入分类" style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:.85rem">
<button id="add-income-cat" style="padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.85rem">添加</button>
</div>
</div>
</div>

<!-- 固定支出 + 数据导出 -->
<div class="card">
<h2>固定支出规则</h2>
<p style="font-size:.8rem;color:var(--text2);margin-bottom:12px">设定分类和描述关键字，系统自动识别固定支出</p>
<div id="bank-fixed-rules" class="cat-list"></div>
<div style="display:flex;gap:8px;margin-top:8px">
<select id="fixed-rule-cat" style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:.85rem">
{"".join(f'<option value="{c}">{c}</option>' for c in sorted_cats)}
</select>
<input type="text" id="fixed-rule-desc" placeholder="描述关键字(可选)" style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:.85rem">
<button id="add-fixed-rule" style="padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.85rem">添加</button>
</div>

<h2 style="margin-top:24px">描述标签</h2>
<div id="bank-desc-tags" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"></div>
<div style="display:flex;gap:8px;margin-top:8px">
<input type="text" id="new-desc-tag" placeholder="添加描述标签" style="flex:1;padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:.85rem">
<button id="add-desc-tag" style="padding:6px 14px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.85rem">添加</button>
</div>

<h2 style="margin-top:24px">数据导出</h2>
<button id="export-json" style="padding:10px 20px;background:var(--green);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.9rem;margin-top:8px">
<i class="fas fa-download"></i> 导出为 JSON
</button>
</div>
</div>

</div><!-- /tab-settings -->

</main>

<div id="notify" class="notification hidden"></div>

<div id="confirm-dlg" class="dialog-overlay hidden">
<div class="dialog-box">
<h3 id="confirm-title">确认操作</h3>
<p id="confirm-msg">确定要执行此操作吗？</p>
<div class="btns">
<button class="btn-cancel" id="confirm-cancel">取消</button>
<button class="btn-ok" id="confirm-ok">确认</button>
</div>
</div>
</div>

<script>
// 嵌入银行交易数据（由 Python 生成，只读）
const RAW_TRANSACTIONS = {txns_json};
</script>

<script>
(function(){{
'use strict';

/* ── Data normalization ── */
var transactions = RAW_TRANSACTIONS.map(function(t) {{
  return {{
    date: t.booking_date,
    type: t.amount >= 0 ? 'income' : 'expense',
    category: t.category || '其他',
    amount: Math.abs(t.amount),
    merchant: t.merchant || '',
    description: t.merchant || '',
    account: t.account || 'DB',
    isInternal: t.is_internal_transfer || false,
  }};
}});

/* ── Theme toggle ── */
(function(){{
  var html=document.documentElement, btn=document.getElementById('themeToggle');
  var saved=localStorage.getItem('bankTheme');
  if (!saved) saved='dark';
  html.setAttribute('data-theme', saved);
  btn.innerHTML = saved==='dark' ? '&#9789;' : '&#9728;';
  btn.addEventListener('click',function(){{
    var cur=html.getAttribute('data-theme');
    var next = cur==='dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    btn.innerHTML = next==='dark' ? '&#9789;' : '&#9728;';
    localStorage.setItem('bankTheme', next);
  }});
}})();

/* ── Tab switching ── */
document.querySelector('.tabs').addEventListener('click',function(e){{
  var btn=e.target.closest('button');
  if(!btn)return;
  var tabId=btn.dataset.tab;
  document.querySelectorAll('.tab-content').forEach(function(t){{t.classList.remove('active-tab')}});
  document.getElementById(tabId).classList.add('active-tab');
  document.querySelectorAll('.tabs button').forEach(function(b){{b.classList.remove('active')}});
  btn.classList.add('active');
}});

var tbody=document.querySelector('tbody');
var rows=Array.from(tbody.querySelectorAll('tr'));
var searchInput=document.getElementById('searchInput');
var searchClear=document.getElementById('searchClear');
var tblCount=document.getElementById('tblCount');
var catFilters=document.getElementById('catFilters');
var activeCat='';
var sortCol=null,sortDir=1;

/* ── Category filter ── */
catFilters.addEventListener('click',function(e){{
  var btn=e.target.closest('button');
  if(!btn)return;
  catFilters.querySelectorAll('button').forEach(function(b){{b.classList.remove('active')}});
  btn.classList.add('active');
  activeCat=btn.dataset.cat;
  applyFilters();
}});

/* ── Search ── */
var searchTimer;
searchInput.addEventListener('input',function(){{
  clearTimeout(searchTimer);
  searchTimer=setTimeout(applyFilters,150);
  searchClear.classList.toggle('visible',this.value.length>0);
}});
searchClear.addEventListener('click',function(){{
  searchInput.value='';
  searchClear.classList.remove('visible');
  applyFilters();
}});

/* ── Table sort ── */
document.querySelector('thead').addEventListener('click',function(e){{
  var th=e.target.closest('th');
  if(!th)return;
  var col=parseInt(th.dataset.col);
  if(sortCol===col){{sortDir*=-1}}else{{sortCol=col;sortDir=1}}
  document.querySelectorAll('thead th').forEach(function(h){{h.classList.remove('sorted')}});
  th.classList.add('sorted');

  var frag=document.createDocumentFragment();
  var sorted=rows.slice().sort(function(a,b){{
    var va=getCellVal(a,col),vb=getCellVal(b,col);
    if(typeof va==='number')return (va-vb)*sortDir;
    return String(va).localeCompare(String(vb),'zh-CN')*sortDir;
  }});
  sorted.forEach(function(r){{frag.appendChild(r)}});
  tbody.appendChild(frag);
}});

function getCellVal(row,col){{
  if(col===3)return parseFloat(row.cells[3].dataset.amount)||0;
  return row.cells[col].textContent.trim();
}}

/* ── Combined filter ── */
function applyFilters(){{
  var q=searchInput.value.toLowerCase().trim();
  var account = document.getElementById('report-account').value;
  var visible=0;
  rows.forEach(function(r){{
    var matchAcct=account==='all'||r.dataset.account===account;
    var matchCat=!activeCat||r.dataset.category===activeCat;
    var matchSearch=!q||r.textContent.toLowerCase().indexOf(q)!==-1;
    var show=matchAcct&&matchCat&&matchSearch;
    r.classList.toggle('hidden',!show);
    if(show)visible++;
  }});
  tblCount.textContent=visible+' / '+rows.length+' 条记录';
}}
/* ── Report view update ── */
function updateReport() {{
  var account = document.getElementById('report-account').value;
  var year = document.getElementById('report-year').value;
  var month = document.getElementById('report-month').value;
  var cat = document.getElementById('report-category').value;
  var search = document.getElementById('report-search').value.toLowerCase().trim();
  var amtMin = parseFloat(document.getElementById('report-amt-min').value) || 0;
  var amtMax = parseFloat(document.getElementById('report-amt-max').value) || Infinity;

  var filtered = transactions.filter(function(t) {{
    if (account !== 'all' && t.account !== account) return false;
    if (year !== 'all' && t.date.substring(0,4) !== year) return false;
    if (month !== 'all' && t.date.substring(0,7) !== month) return false;
    if (cat !== 'all' && t.category !== cat) return false;
    if (search && t.merchant.toLowerCase().indexOf(search) === -1) return false;
    if (t.amount < amtMin || t.amount > amtMax) return false;
    return true;
  }});

  // 排除内部转账
  var extFiltered = filtered.filter(function(t) {{ return !t.isInternal; }});
  var totalIn = 0, totalOut = 0;
  var catTotals = {{}};
  var catCounts = {{}};
  extFiltered.forEach(function(t) {{
    if (t.type === 'income') {{ totalIn += t.amount; }}
    else {{ totalOut += t.amount; }}
    catTotals[t.category] = (catTotals[t.category] || 0) + t.amount;
    catCounts[t.category] = (catCounts[t.category] || 0) + 1;
  }});

  document.getElementById('rpt-income').textContent = '€' + totalIn.toFixed(2);
  document.getElementById('rpt-expense').textContent = '€' + totalOut.toFixed(2);
  var bal = totalIn - totalOut;
  var balEl = document.getElementById('rpt-balance');
  balEl.textContent = '€' + (bal >= 0 ? '+' : '') + bal.toFixed(2);
  document.getElementById('rpt-count').textContent = filtered.length;

  // Category detail table
  var totalExpense = totalOut;
  var tbody = document.getElementById('rpt-expense-detail');
  tbody.innerHTML = '';
  var sorted = Object.keys(catTotals).sort(function(a,b) {{ return catTotals[b] - catTotals[a]; }});
  sorted.forEach(function(cat) {{
    var amt = catTotals[cat];
    var pct = totalExpense > 0 ? (amt / totalExpense * 100).toFixed(1) : '0.0';
    var tr = document.createElement('tr');
    tr.innerHTML = '<td>' + cat + '</td>' +
      '<td style="text-align:right;color:var(--red);font-weight:600">€' + amt.toFixed(2) + '</td>' +
      '<td style="text-align:right">' + pct + '%</td>' +
      '<td style="text-align:right">' + (catCounts[cat] || 0) + '</td>';
    tbody.appendChild(tr);
  }});

  // Dynamic pie charts
  updatePieChart('rpt-expense-pie', 'expense', extFiltered);
  updatePieChart('rpt-income-pie', 'income', extFiltered);
}}

function updatePieChart(divId, type, txns) {{
  var catData = {{}};
  txns.filter(function(t) {{ return t.type === type; }}).forEach(function(t) {{
    catData[t.category] = (catData[t.category] || 0) + t.amount;
  }});
  var labels = Object.keys(catData);
  var values = Object.values(labels.map(function(k) {{ return catData[k]; }}));
  // Actually get values properly
  values = labels.map(function(k) {{ return catData[k]; }});

  var colors = type === 'expense'
    ? ['#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#10b981','#14b8a6','#06b6d4','#3b82f6','#6366f1','#8b5cf6','#a855f7','#d946ef','#ec4899']
    : ['#10b981','#22c55e','#84cc16','#14b8a6','#06b6d4','#3b82f6','#6366f1','#8b5cf6','#a855f7','#d946ef'];

  var total = values.reduce(function(a,b) {{ return a+b; }}, 0);

  var data = [{{
    type: 'pie',
    labels: labels,
    values: values,
    hole: 0.45,
    textinfo: labels.length <= 8 ? 'label+percent' : 'percent',
    textposition: labels.length <= 8 ? 'auto' : 'outside',
    textfont: {{size: 11}},
    marker: {{colors: colors.slice(0, labels.length)}},
    automargin: true,
  }}];

  var layout = {{
    height: 440,
    margin: {{l: 20, r: 80, t: 10, b: 20}},
    showlegend: true,
    legend: {{orientation: 'v', y: 0.5, x: 1.05, xanchor: 'left'}},
    template: 'plotly_white',
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
  }};

  var config = {{displayModeBar: false, responsive: true}};

  Plotly.react(divId, data, layout, config).then(null, function() {{
    // If react fails (first render), use newPlot
    Plotly.newPlot(divId, data, layout, config);
  }});
}}

// Wire report filters
document.getElementById('report-account').addEventListener('change', function() {{
  updateReport();
  updateYearlyStats();
  applyFilters();
}});
document.getElementById('report-year').addEventListener('change', function() {{
  updateReport();
  updateYearlyStats();
  // 切换年度趋势图
  var yr = this.value;
  document.querySelectorAll('.yearly-chart').forEach(function(el) {{
    el.style.display = (yr === 'all' || el.dataset.year === yr) ? 'block' : 'none';
  }});
}});
document.getElementById('report-month').addEventListener('change', updateReport);
document.getElementById('report-category').addEventListener('change', updateReport);
document.getElementById('report-search').addEventListener('input', function() {{
  clearTimeout(this._timer);
  this._timer = setTimeout(updateReport, 200);
}});
document.getElementById('report-amt-min').addEventListener('input', function() {{
  clearTimeout(this._timer2);
  this._timer2 = setTimeout(updateReport, 300);
}});
document.getElementById('report-amt-max').addEventListener('input', function() {{
  clearTimeout(this._timer3);
  this._timer3 = setTimeout(updateReport, 300);
}});

// Initial report render
updateReport();

/* ── Yearly statistics ── */
function updateYearlyStats() {{
  var account = document.getElementById('report-account').value;
  var selectedYear = document.getElementById('report-year').value;
  if (selectedYear === 'all') selectedYear = String(new Date().getFullYear());
  var yrTxns = transactions.filter(function(t) {{
    if (account !== 'all' && t.account !== account) return false;
    return t.date.substring(0,4) == selectedYear;
  }});
  var yrIn = 0, yrOut = 0;
  var expCat = {{}}, incCat = {{}};
  yrTxns.forEach(function(t) {{
    if (t.type === 'income') {{ yrIn += t.amount; incCat[t.category] = (incCat[t.category] || 0) + t.amount; }}
    else {{ yrOut += t.amount; expCat[t.category] = (expCat[t.category] || 0) + t.amount; }}
  }});
  document.getElementById('yr-income').textContent = '€' + yrIn.toFixed(2);
  document.getElementById('yr-expense').textContent = '€' + yrOut.toFixed(2);
  var yrBal = yrIn - yrOut;
  document.getElementById('yr-balance').textContent = '€' + (yrBal >= 0 ? '+' : '') + yrBal.toFixed(2);

  // Category breakdown tables
  function fillTable(tbodyId, catData, total) {{
    var tbody = document.getElementById(tbodyId);
    tbody.innerHTML = '';
    var sorted = Object.keys(catData).sort(function(a,b) {{ return catData[b] - catData[a]; }});
    sorted.forEach(function(cat) {{
      var amt = catData[cat];
      var pct = total > 0 ? (amt / total * 100).toFixed(1) : '0.0';
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + cat + '</td><td style="text-align:right;font-weight:600">€' + amt.toFixed(2) + '</td><td style="text-align:right">' + pct + '%</td>';
      tbody.appendChild(tr);
    }});
  }}
  fillTable('yr-exp-detail', expCat, yrOut);
  fillTable('yr-inc-detail', incCat, yrIn);

  // Monthly comparison table
  var months = ['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'];
  var mtbody = document.getElementById('yr-monthly-detail');
  mtbody.innerHTML = '';
  var yIn = 0, yOut = 0;
  months.forEach(function(mName, i) {{
    var mNum = String(i + 1).padStart(2, '0');
    var mIn = 0, mOut = 0;
    yrTxns.forEach(function(t) {{
      if (t.date.substring(5,7) === mNum) {{
        if (t.type === 'income') mIn += t.amount; else mOut += t.amount;
      }}
    }});
    yIn += mIn; yOut += mOut;
    var mBal = mIn - mOut;
    var tr = document.createElement('tr');
    tr.innerHTML = '<td>' + mName + '</td>' +
      '<td style="text-align:right;color:var(--green)">' + (mIn > 0 ? '€' + mIn.toFixed(2) : '-') + '</td>' +
      '<td style="text-align:right;color:var(--red)">' + (mOut > 0 ? '€' + mOut.toFixed(2) : '-') + '</td>' +
      '<td style="text-align:right;font-weight:600;color:' + (mBal >= 0 ? 'var(--green)' : 'var(--red)') + '">€' + (mBal >= 0 ? '+' : '') + mBal.toFixed(2) + '</td>';
    mtbody.appendChild(tr);
  }});
  // Total row
  var tBal = yIn - yOut;
  var tr = document.createElement('tr');
  tr.style.cssText = 'background:var(--bg);font-weight:700;border-top:2px solid var(--border)';
  tr.innerHTML = '<td>全年合计</td>' +
    '<td style="text-align:right;color:var(--green)">€' + yIn.toFixed(2) + '</td>' +
    '<td style="text-align:right;color:var(--red)">€' + yOut.toFixed(2) + '</td>' +
    '<td style="text-align:right;color:' + (tBal >= 0 ? 'var(--green)' : 'var(--red)') + '">€' + (tBal >= 0 ? '+' : '') + tBal.toFixed(2) + '</td>';
  mtbody.appendChild(tr);
}}
updateYearlyStats();

// 初始显示对应年份的趋势图
(function() {{
  var yr = document.getElementById('report-year').value;
  document.querySelectorAll('.yearly-chart').forEach(function(el) {{
    el.style.display = (yr === 'all' || el.dataset.year === yr) ? 'block' : 'none';
  }});
}})();

/* ── Settings: localStorage helpers ── */
function loadPrefs() {{
  try {{ return JSON.parse(localStorage.getItem('bankPrefs') || '{{}}'); }} catch(e) {{ return {{}}; }}
}}
function savePrefs(p) {{ localStorage.setItem('bankPrefs', JSON.stringify(p)); }}
var prefs = loadPrefs();

// Default categories derived from transaction data
var allExpCats = transactions.filter(function(t){{return t.type==='expense'}}).map(function(t){{return t.category}});
var allIncCats = transactions.filter(function(t){{return t.type==='income'}}).map(function(t){{return t.category}});
var defaultExpCats = []; allExpCats.forEach(function(c){{ if(defaultExpCats.indexOf(c)<0) defaultExpCats.push(c); }});
var defaultIncCats = []; allIncCats.forEach(function(c){{ if(defaultIncCats.indexOf(c)<0) defaultIncCats.push(c); }});
var defaultTags = [];
transactions.forEach(function(t){{ if(t.merchant && defaultTags.indexOf(t.merchant)<0) defaultTags.push(t.merchant); }});

var expCats = prefs.expCats || defaultExpCats.slice();
var incCats = prefs.incCats || defaultIncCats.slice();
var descTags = prefs.descTags || defaultTags.slice();
var fixedRules = prefs.fixedRules || [];

/* ── Settings: category list render ── */
function renderCatList(containerId, cats, type) {{
  var container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  cats.forEach(function(cat, i) {{
    var div = document.createElement('div');
    div.className = 'cat-item';
    div.innerHTML = '<span>' + cat + '</span><div>' +
      '<button class="edit-btn" data-idx="' + i + '" data-type="' + type + '"><i class="fas fa-edit"></i></button>' +
      '<button class="del-btn" data-idx="' + i + '" data-type="' + type + '"><i class="fas fa-trash"></i></button></div>';
    container.appendChild(div);
  }});
}}

function renderAllSettings() {{
  renderCatList('bank-expense-cats', expCats, 'expense');
  renderCatList('bank-income-cats', incCats, 'income');
  renderFixedRules();
  renderTags();
}}
renderAllSettings();

/* ── Settings: category CRUD ── */
function addCategory(type) {{
  var inputId = type === 'expense' ? 'new-expense-cat' : 'new-income-cat';
  var input = document.getElementById(inputId);
  var name = input.value.trim();
  if (!name) return;
  var list = type === 'expense' ? expCats : incCats;
  if (list.indexOf(name) >= 0) {{ notify('分类已存在'); return; }}
  list.push(name);
  input.value = '';
  persistAndRender();
  notify('分类已添加');
}}

document.getElementById('add-expense-cat').addEventListener('click', function() {{ addCategory('expense'); }});
document.getElementById('add-income-cat').addEventListener('click', function() {{ addCategory('income'); }});
document.getElementById('new-expense-cat').addEventListener('keydown', function(e) {{ if(e.key==='Enter') addCategory('expense'); }});
document.getElementById('new-income-cat').addEventListener('keydown', function(e) {{ if(e.key==='Enter') addCategory('income'); }});

// Delegate edit/delete clicks on category lists
document.getElementById('bank-expense-cats').addEventListener('click', function(e) {{
  var btn = e.target.closest('button');
  if (!btn) return;
  var idx = parseInt(btn.dataset.idx);
  if (btn.classList.contains('del-btn')) {{ deleteCat('expense', idx); }}
  else if (btn.classList.contains('edit-btn')) {{ editCat('expense', idx); }}
}});
document.getElementById('bank-income-cats').addEventListener('click', function(e) {{
  var btn = e.target.closest('button');
  if (!btn) return;
  var idx = parseInt(btn.dataset.idx);
  if (btn.classList.contains('del-btn')) {{ deleteCat('income', idx); }}
  else if (btn.classList.contains('edit-btn')) {{ editCat('income', idx); }}
}});

function deleteCat(type, idx) {{
  var list = type === 'expense' ? expCats : incCats;
  confirmDlg('删除分类', '删除 "' + list[idx] + '"？', function() {{
    list.splice(idx, 1);
    persistAndRender();
    notify('分类已删除');
  }});
}}

function editCat(type, idx) {{
  var list = type === 'expense' ? expCats : incCats;
  var oldName = list[idx];
  var newName = prompt('重命名分类', oldName);
  if (newName && newName.trim() && newName.trim() !== oldName) {{
    list[idx] = newName.trim();
    persistAndRender();
    notify('分类已更新');
  }}
}}

/* ── Fixed expense rules ── */
function renderFixedRules() {{
  var container = document.getElementById('bank-fixed-rules');
  if (!container) return;
  container.innerHTML = '';
  fixedRules.forEach(function(rule, i) {{
    var div = document.createElement('div');
    div.className = 'cat-item';
    div.innerHTML = '<span>' + rule.cat + (rule.desc ? ' (' + rule.desc + ')' : '') + '</span>' +
      '<button class="del-btn" data-idx="' + i + '"><i class="fas fa-trash"></i></button>';
    container.appendChild(div);
  }});
}}

document.getElementById('bank-fixed-rules').addEventListener('click', function(e) {{
  var btn = e.target.closest('button');
  if (!btn) return;
  fixedRules.splice(parseInt(btn.dataset.idx), 1);
  persistAndRender();
  notify('规则已删除');
}});

document.getElementById('add-fixed-rule').addEventListener('click', function() {{
  var cat = document.getElementById('fixed-rule-cat').value;
  var desc = document.getElementById('fixed-rule-desc').value.trim();
  var dup = fixedRules.some(function(r) {{ return r.cat === cat && r.desc === desc; }});
  if (dup) {{ notify('规则已存在'); return; }}
  fixedRules.push({{cat: cat, desc: desc}});
  document.getElementById('fixed-rule-desc').value = '';
  persistAndRender();
  notify('规则已添加');
}});

/* ── Description tags ── */
function renderTags() {{
  var container = document.getElementById('bank-desc-tags');
  if (!container) return;
  container.innerHTML = '';
  descTags.forEach(function(tag, i) {{
    var span = document.createElement('span');
    span.className = 'tag-chip';
    span.innerHTML = tag + '<span class="rm" data-idx="' + i + '">&times;</span>';
    container.appendChild(span);
  }});
}}

document.getElementById('bank-desc-tags').addEventListener('click', function(e) {{
  if (e.target.classList.contains('rm')) {{
    descTags.splice(parseInt(e.target.dataset.idx), 1);
    persistAndRender();
    notify('标签已删除');
  }}
}});

document.getElementById('add-desc-tag').addEventListener('click', function() {{
  var input = document.getElementById('new-desc-tag');
  var tag = input.value.trim();
  if (!tag) return;
  if (descTags.indexOf(tag) >= 0) {{ notify('标签已存在'); return; }}
  descTags.push(tag);
  input.value = '';
  persistAndRender();
  notify('标签已添加');
}});

document.getElementById('new-desc-tag').addEventListener('keydown', function(e) {{
  if (e.key === 'Enter') document.getElementById('add-desc-tag').click();
}});

/* ── Export ── */
document.getElementById('export-json').addEventListener('click', function() {{
  var data = {{transactions: RAW_TRANSACTIONS, expenseCategories: expCats, incomeCategories: incCats, descriptionTags: descTags, fixedExpenseRules: fixedRules}};
  var blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'bank_report_' + new Date().toISOString().slice(0,10) + '.json';
  a.click();
  notify('数据已导出');
}});

/* ── Persist & notify helpers ── */
function persistAndRender() {{
  prefs.expCats = expCats;
  prefs.incCats = incCats;
  prefs.descTags = descTags;
  prefs.fixedRules = fixedRules;
  savePrefs(prefs);
  renderAllSettings();
}}

function notify(msg) {{
  var el = document.getElementById('notify');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(el._timer);
  el._timer = setTimeout(function() {{ el.classList.add('hidden'); }}, 2000);
}}

function confirmDlg(title, msg, cb) {{
  var dlg = document.getElementById('confirm-dlg');
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-msg').textContent = msg;
  dlg.classList.remove('hidden');
  document.getElementById('confirm-cancel').onclick = function() {{ dlg.classList.add('hidden'); }};
  document.getElementById('confirm-ok').onclick = function() {{ dlg.classList.add('hidden'); cb(); }};
}}

}})();
</script>

</body>
</html>"""


# ── 后处理 ──────────────────────────────────────────────────────────────────

# 从刷卡记录详情中提取商户名
CARD_STORE_F1_RE = re.compile(r'Payment details\s+(.+?)/')    # 格式一: "Payment details STORE//..." 或 "STORE/CITY..."
CARD_STORE_F2_RE = re.compile(r'^([^/]+?)/')                   # 格式二: "STORE//CITY/DE" 或 "STORE/CITY/DE"


def post_process(transactions: list[dict]) -> list[dict]:
    """清洗商户名、去重."""
    for t in transactions:
        merchant = t.get('merchant', '')
        details = t.get('details', '')

        # 格式一 Debit Card Payment: merchant 行就是 "Debit Card Payment"
        if merchant == 'Debit Card Payment':
            m = CARD_STORE_F1_RE.search(details)
            if m:
                t['merchant'] = m.group(1).strip()

        # 格式二 Kartenzahlung: 商户名误提取为 Payment Reference
        elif merchant == 'Payment Reference/E2E-Ref.':
            m = CARD_STORE_F2_RE.search(details)
            if m:
                t['merchant'] = m.group(1).strip()

        # 格式二 Kartenzahlung: 空商户名
        elif t.get('type') == 'Debit Card Payment' and not merchant:
            m = CARD_STORE_F2_RE.search(details)
            if m:
                t['merchant'] = m.group(1).strip()

    # 去重: (日期, 金额±0.01, 商户前15字符)
    seen = {}
    deduped = []
    for t in transactions:
        key = (t['booking_date'], round(t['amount'], 2), t['merchant'][:15])
        if key not in seen:
            seen[key] = t
            deduped.append(t)
    return deduped


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="银行流水 PDF → 可视化 HTML 报告")
    parser.add_argument('--force', action='store_true', help='强制重新解析所有 PDF')
    parser.add_argument('--output', type=str, default=None, help='输出 HTML 路径')
    parser.add_argument('--month', type=str, default=None, help='仅输出指定月份 (YYYY-MM)')
    args = parser.parse_args()

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"[错误] {PDF_DIR} 中没有 PDF 文件")
        sys.exit(1)

    # 加载缓存
    cache = load_cache(force=args.force)
    all_txns = []

    if cache and not args.force:
        all_txns = cache
        print(f"[OK] 缓存命中 - {len(all_txns)} 笔交易")
    else:
        for pdf_path in pdf_files:
            print(f"-> 解析 {pdf_path.name}...")
            doc = fitz.open(str(pdf_path))
            full_text = "\n".join(page.get_text() for page in doc)
            doc.close()

            fmt = detect_format(full_text)
            if fmt == "transactions":
                txns = parse_transactions_format(full_text)
            elif fmt == "account_statement":
                txns = parse_account_statement_format(full_text)
            else:
                print(f"  [!] 未知格式 - 将尝试用 MinerU (magic-pdf) 作为备选")
                # ponytail: MinerU fallback — 按需实现
                txns = []

            print(f"  -> 提取 {len(txns)} 笔交易 (格式: {fmt})")
            # 标记账户来源
            for t in txns:
                t['account'] = 'ME'
                t['is_internal_transfer'] = False
            all_txns.extend(txns)

        # 解析 Trade Republic CSV（所有 .csv 文件）
        for csv_path in sorted(PDF_DIR.glob("*.csv")):
            print(f"-> 解析 {csv_path.name}...")
            tr_txns = parse_trade_republic_csv(csv_path)
            # 根据文件名区分账户：含 -cr 的是老婆，其余是自己的
            if '-cr' in csv_path.stem:
                acct = 'WIFE'
            else:
                acct = 'ME'
            for t in tr_txns:
                t['account'] = acct
            print(f"  -> 提取 {len(tr_txns)} 笔 CASH 交易 ({acct})")
            all_txns.extend(tr_txns)

        # 检测 PDF 侧的内部转账
        detect_internal_transfers(all_txns)

        # 后处理：清洗商户名 + 去重
        all_txns = post_process(all_txns)

        # 分类
        for t in all_txns:
            t['category'] = categorize(t.get('merchant', ''), t['amount'], t.get('details', ''))

        # 存缓存
        save_cache(all_txns)
        internal_count = sum(1 for t in all_txns if t.get('is_internal_transfer'))
        print(f"[OK] 缓存已保存 ({len(all_txns)} 笔，其中 {internal_count} 笔内部转账)")

    # 按月份筛选
    if args.month:
        all_txns = [t for t in all_txns if t['booking_date'].startswith(args.month)]
        if not all_txns:
            print(f"[错误] {args.month} 没有交易数据")
            sys.exit(1)
        print(f"-> 筛选 {args.month}: {len(all_txns)} 笔")

    # 生成报告
    output = Path(args.output) if args.output else OUTPUT_FILE
    html = build_report(all_txns)
    output.write_text(html, encoding='utf-8')
    total_in = sum(t['amount'] for t in all_txns if t['amount'] > 0)
    total_out = abs(sum(t['amount'] for t in all_txns if t['amount'] < 0))
    print(f"\n[OK] 报告已生成: {output}")
    print(f"   收入: €{total_in:,.2f}  支出: €{total_out:,.2f}  净额: €{total_in-total_out:+,.2f}")


if __name__ == '__main__':
    main()
