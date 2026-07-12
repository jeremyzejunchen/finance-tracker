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
SIMPLE_OUTPUT = SCRIPT_DIR / "财务简表.html"

# PayPal CSV 内部交易类型（不计入收支，新 CSV 格式 2026-07）
# 新 CSV 无 Status/Balance Impact 列，类型在 Description (EN) / Beschreibung (DE) 中
PAYPAL_INTERNAL_TYPES = {
    # 英文 (ME 账户)
    'Bank Deposit to PP Account',
    'General Card Deposit',
    'General Card Withdrawal',
    'User Initiated Withdrawal',
    'Reversal of ACH Deposit',
    'Reversal of ACH Withdrawal Transaction',
    'Account Hold for Open Authorization',
    'Reversal of General Account Hold',
    'General Authorization',
    # 德文 (WIFE 账户)
    'Bankgutschrift auf PayPal-Konto',
    'Allgemeine Gutschrift auf Kreditkarte',
    'Von Nutzer eingeleitete Abbuchung',
    'Rückbuchung von ACH-Gutschrift',
    'Einbehaltung für offene Autorisierung',
    'Rückbuchung allgemeiner Einbehaltung',
    'ACH-Überweisung als Zahlungsquelle für Ausgleich von Kontoguthaben',
    'Allgemeine Autorisierung',
}

# 德文 Beschreibung → 英文标准名映射（WIFE 账户 CSV 类型名统一化）
PP_TYPE_DE_TO_EN = {
    'PayPal Express-Zahlung': 'Express Checkout Payment',
    'Handyzahlung': 'Mobile Payment',
    'Allgemeine Zahlung': 'General Payment',
    'Zahlung im Einzugsverfahren mit Zahlungsrechnung': 'PreApproved Payment Bill User Payment',
}

# ── 自有 IBAN（用于识别账户间内部转账）──────────────────────────────────────
OWN_IBANS = {
    "DE64290700240344376900",  # DB
    "DE79100123455797203011",  # MY-TR
    "DE08100123456340785111",  # WIFE-TR
}

# ── 分类规则 ─────────────────────────────────────────────────────────────────
# 两层结构: 第一层(固定支出/活动支出/收入) → 第二层(具体子类)
# 编辑 categories.md 后告诉 Claude，自动同步到这里

EXPENSE_RULES = [
    # === 固定支出 ===
    ("房租", ["HARALD WINDEL", "CATELLA REAL ESTATE"]),
    ("电费", ["E.ON ENERGIE", "STADTWERKE"]),
    ("广电费", ["RUNDFUNK"]),
    ("网费", ["TELEKOM DEUTSCHLAND"]),
    ("话费", ["VODAFONE"]),
    ("健康保险", ["TECHNIKER KRANKENKASSE"]),
    ("第三方责任险", ["GOTHAER ALLGEMEINE"]),
    ("其他保险", ["SIGNAL IDUNA"]),
    ("车险", ["VOLKSWAGEN AUTOVERSICHERUNG", "VOLKSWAGEN AUTOVERS.", "HUK-COBURG",
              "SPARKASSEN DIREKTVERSICHERUNG", "CONTINENTALE"]),
    ("汽车保养", ["VW LEASING", "SUEDHANNOVER"]),
    ("健身", ["FINION CAPITAL", "FITNESS FUTURE", "A.I. FITNESS"]),
    # === 活动支出 ===
    ("家居用品", ["IKEA", "OBI"]),
    ("超市日用品", ["KAUFLAND", "LIDL", "ALDI", "REWE", "DM-DROGERIE", "DM DROGERIE", "GO ASIA",
                    "EDEKA", "NETTO", "ROSSMANN", "PENNY", "ACTION", "HANDELSHOF",
                    "TEGUT", "MIX MARKT", "TANIA MOHAMED"]),
    ("线上购物", ["AMAZON", "ALIEXPRESS", "EBAY", "TAOBAO", "ZALANDO", "UNIQLO",
                  "AMZN MKTP", "SP ORCHIDEEN-KLUSMAN", "SP CHINA MARKT CHEMN",
                  "NESPRESSO", "PAYPAL *ALIPAY", "DELOOX", "INFIGO", "OCHAMA", "DOCMORRIS",
                  "JOYBUY", "FERA", "JELLYCAT", "SMYTHS", "THG BEAUTY", "LOOKFANTASTIC",
                  "VINTED", "KLEIDERKREISEL", "HERMANN COMMERCE",
                  # 个人商家
                  "YI HE", "BIRONG XU", "DENNIS SCHWARZE", "JULIETTE OWCZAREK",
                  "ANGELINA JOVANOVIC", "FAN SHI"]),
    ("线下购物", ["PRIMARK", "DOUGLAS"]),
    ("餐饮外食", ["KFC", "MCDONALD", "BURGER KING", "UBER", "LIEFERANDO",
                  "UMG GASTRONOMIE", "CAFE", "LUTZ MICHAEL",
                  "ROXX", "DOOBOO THE GLEN", "CHILICHILLEN HOTPOT", "EISCAFE",
                  "SPC.MAMAME", "COCO TEA FRESH", "PAMPAS GENT", "THE OLIVE STREETFOOD",
                  "PAVILLON STADT"]),
    ("油费", ["ARAL", "SHELL", "TOTAL", "BFT", "PFEFFER", "STAR GOTTINGEN",
              "OIL 413", "CLASSIC TANKSTELLE", "ESSO", "LEO HERZBERG",
              "JET-TANKSTELLE"]),
    ("停车费", ["PARKSTER", "PARKDEPOT", "CONTRIPARK", "CONTIPARK", "PARKAUTOMATEN", "PARKEN",
                "PARKING", "BEZIRKSAMT CHARLOTTENBURG"]),
    ("汽车税", ["BUNDESKASSE", "KFZ-STEUER"]),
    ("罚款", ["STADT KASSEL VERKEHRSUEBERW"]),
    ("公共交通", ["DEUTSCHE BAHN", "DB VERTRIEB", "DE LIJN"]),
    ("宠物保险", ["HANSEMERKUR"]),
    ("宠物食品", ["FRESSNAPF", "ZOOPLUS", "ZOOROYAL", "CATAMORE", "FOX4PETS",
                  "GRANATAPET", "ZOOLAND"]),
    ("兽医", ["TIERARZT", "TIERAERZTLICHES", "DR. MED. VET. WYSTUB"]),
    ("医疗", ["APOTHEKE", "KRANKENHAUS", "SANITATSHAUS", "DRK", "ARBEITER-SAMARITER",
              "SHOP APOTHEKE", "APO PHARMACY"]),
    ("旅行", ["BOOKING", "AIRBNB", "LUFTHANSA", "HOLIDAY INN", "CHECK24",
              "GOODMORNINGBERLIN", "PREUSS.SCHLOSSER", "HAMB. ELBPHILHARMONIE", "HEADOUT"]),
    ("服饰", ["LULULEMON", "ZALANDO", "PRIMARK", "UNIQLO", "ARCTERYX", "RALPH LAUREN", "RL FINANCE"]),
    ("娱乐", ["NETFLIX", "SPOTIFY", "CINEMAXX", "NEXON"]),
    ("学费", ["GEORG-AUGUST-UNIVERSITAT", "UMG", "GOTTINGEN STIFTUNG", "HEENEMANN"]),
    ("市政缴费", ["STADT GOETTINGEN", "BUERGERBUERO", "GENERALKON. DER VR CHINA"]),
    ("网上充值", ["GOOGLE CLOUD", "APPLE", "OPENAI"]),
    ("邮寄", ["DEUTSCHE POST", "DPD", "HERMES"]),
    ("家人转账", ["RUI CHENG", "ZEJUN CHEN"]),
    ("朋友转账", ["ABDUL", "YANG SUN", "PAYPAL *CATHERINE2013",
                  "YUXIAO LUO", "SIWEN YUAN", "RUI GUO", "TZU-YUEH CHEN",
                  "YAOBIN WU"]),
    ("投资", ["SPACEX"]),
]

INCOME_RULES = [
    ("工资", ["DRES. DEKOWSKI, RENNER"]),
    ("大学薪资", ["GEORG-AUGUST-UNIVERSITAT", "UMG", "GOTTINGEN STIFTUNG"]),
    ("奖学金/津贴", ["SPARKASSEN DIREKTVERSICHERUNG"]),
    ("二手收入", ["MANGOPAY", "DIANA BUCK", "KARIN MOLLBERG", "CHUYAO WAN"]),
    ("投资收入", ["TESLA", "CORE S&P 500", "NETFLIX", "SAVEBACK", "STOCKPERK"]),
    ("利息收入", ["INTEREST PAYMENT", "CREDIT INTEREST", "LEIPZIG ACCOUNT",
                  "BALANCE OF SETTLEMENT ITEMS"]),
    ("朋友转账", ["ABDUL", "YANG SUN", "PAYPAL *CATHERINE2013",
                  "YUXIAO LUO", "SIWEN YUAN", "RUI GUO", "TZU-YUEH CHEN",
                  "YAOBIN WU"]),
    ("家人转账", ["RUI CHENG", "ZEJUN CHEN"]),
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

            # SEPA 扣款 (TRANSFER_DIRECT_DEBIT_INBOUND): CSV name 列是账户持有人，
            # 真正商户在 description 中 "transfer to <NAME> (IBAN)"，提取后用于分类
            if txn_type == 'TRANSFER_DIRECT_DEBIT_INBOUND':
                m = re.search(r'transfer to (.+?) \(', description)
                if m:
                    merchant = m.group(1).strip()

            # 检测内部转账（排除 SEPA 扣款，其 counterparty_iban 是自有 IBAN 但交易本身是外部支付）
            is_internal = (
                'TRANSFER' in txn_type and
                'DIRECT_DEBIT' not in txn_type and
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


# ═══════════════════════════════════════════════════════════════════════════════
# CSV 解析器: PayPal 导出
# ═══════════════════════════════════════════════════════════════════════════════

def parse_paypal_csv(csv_path: Path) -> list[dict]:
    """解析 PayPal CSV 导出（新格式 2026-07） → 统一交易格式。

    新 CSV 格式特点：
    - 列名: Description/Beschreibung (类型), Gross/Brutto (金额), Balance/Guthaben (累计余额)
    - 无 Status 列（所有行均已完成）
    - 无 Balance Impact 列（方向由 Gross 符号: 正=收入, 负=支出）
    - 同时支持英文 (ME) 和德文 (WIFE) 列名
    """
    import csv as _csv
    txns = []

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = _csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        # 检测语言：德文 CSV 用 "Datum" 而非 "Date"
        is_german = any('Datum' in (fn or '') for fn in fieldnames)

        # 新 CSV 列名映射（无 Status / Balance Impact 列）
        col_desc    = 'Beschreibung' if is_german else 'Description'
        col_gross   = 'Brutto' if is_german else 'Gross'
        col_date    = 'Datum' if is_german else 'Date'
        col_name    = 'Name'  # 两种语言一致
        col_txn_id  = 'Transaktionscode' if is_german else 'Transaction ID'
        col_balance = 'Guthaben' if is_german else 'Balance'
        col_email   = 'Absender E-Mail-Adresse' if is_german else 'From Email Address'

        # 检测账户：通过发件邮箱或文件名判断
        from_email = ''
        for row in reader:
            from_email = row.get(col_email, '').strip()
            if from_email:
                break
        f.seek(0)
        reader = _csv.DictReader(f)

        account = 'WIFE' if ('chengrui' in from_email.lower() or '-cr' in csv_path.stem) else 'ME'

        for row in reader:
            desc = row.get(col_desc, '').strip()

            # 跳过内部交易（充值/提现/冻结/逆转等 PayPal 内部操作）
            if desc in PAYPAL_INTERNAL_TYPES:
                continue
            # 新 CSV 无 Status 列，不需要状态检查

            # 德文类型名统一映射为英文标准名
            if is_german and desc in PP_TYPE_DE_TO_EN:
                desc = PP_TYPE_DE_TO_EN[desc]

            # 解析金额（Gross 符号直接决定方向: 正=收入, 负=支出）
            gross = row.get(col_gross, '0').strip()
            amount = parse_amount_f2(gross)

            # 解析日期（新 CSV 统一 DD.MM.YYYY 格式）
            date_str = row.get(col_date, '').strip()
            booking_date = None
            for fmt in ('%d.%m.%Y', '%d/%m/%Y'):
                try:
                    booking_date = datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
                    break
                except ValueError:
                    continue
            if not booking_date:
                continue

            merchant = row.get(col_name, '').strip()
            txn_id = row.get(col_txn_id, '').strip()

            # Balance = 交易后 PayPal 累计余额（仅做参考，不用于匹配）
            balance_str = row.get(col_balance, '').strip()
            _pp_balance_after = None
            if balance_str:
                try:
                    _pp_balance_after = parse_amount_f2(balance_str)
                except (ValueError, AttributeError):
                    pass

            # 新 CSV 无 Item Title / Note 列，详情仅含交易 ID
            details = f'PP-TxnID: {txn_id}'

            txns.append({
                'booking_date': booking_date,
                'value_date': booking_date,
                'amount': amount,
                'type': desc,
                'merchant': merchant,
                'details': details,
                'source_fmt': 'paypal_csv',
                'account': account,
                'is_internal_transfer': False,
                '_pp_txn_id': txn_id,
                '_pp_balance_after': _pp_balance_after,
            })

    return txns


def _clean_pp_fields(pt: dict) -> dict:
    """剥离 PayPal 内部字段，返回干净的副本。"""
    pt_copy = dict(pt)
    for key in ('_pp_txn_id', '_pp_balance_after'):
        pt_copy.pop(key, None)
    return pt_copy


def match_paypal_to_bank(pp_txns: list[dict], bank_txns: list[dict]) -> tuple[list[dict], list[dict]]:
    """将 PayPal 交易与银行交易匹配，返回 (new_txns, updated_bank_indices)。

    - 匹配成功的支出 → 用 PayPal 商户名更新银行交易的 merchant，重新分类
    - 匹配成功的收入 → 标记银行为内部转账（提现），保留 PayPal 收入
    - 未匹配的交易 → 作为新交易添加到数据中
    - 方向由 amount 符号判断: 正=收入, 负=支出（新 CSV 无 Balance Impact 列）
    """
    from datetime import timedelta as _td

    new_txns = []
    matched_bank_indices = set()
    pp_credits_matched_to_bank_paypal = set()

    # 为银行交易建立 (amount_abs, ...) 索引
    bank_by_abs_amt = {}
    for i, bt in enumerate(bank_txns):
        key = round(abs(bt['amount']), 2)
        bank_by_abs_amt.setdefault(key, []).append(i)

    # 辅助函数：±5 天窗口内查找最佳匹配
    def _find_best_match(abs_amt, pp_date):
        candidates = bank_by_abs_amt.get(abs_amt, [])
        best = None
        for bi in candidates:
            if bi in matched_bank_indices:
                continue
            bt = bank_txns[bi]
            try:
                bd = datetime.strptime(bt['booking_date'], '%Y-%m-%d')
                pd = datetime.strptime(pp_date, '%Y-%m-%d')
            except ValueError:
                continue
            if abs((bd - pd).days) <= 5:
                bank_m = bt.get('merchant', '').upper()
                if 'PAYPAL' in bank_m:
                    return bi  # PayPal 精确匹配 → 立即采用
                if best is None:
                    best = bi
        return best

    for pt in pp_txns:
        pp_amt = pt['amount']
        pp_date = pt['booking_date']
        pp_merchant = pt['merchant']
        abs_amt = round(abs(pp_amt), 2)
        is_credit = pp_amt > 0  # amount 符号替代旧 _pp_impact

        best_match = _find_best_match(abs_amt, pp_date)

        if best_match is not None:
            bt = bank_txns[best_match]
            matched_bank_indices.add(best_match)
            bank_m = bt.get('merchant', '').upper()

            if is_credit and 'PAYPAL' in bank_m:
                # PayPal 收入 → 银行 PAYPAL 收入（提现到银行）
                # 标记银行为内部转账，保留 PayPal 为真实收入
                bt['is_internal_transfer'] = True
                pp_credits_matched_to_bank_paypal.add(pt['_pp_txn_id'])
                pt_copy = _clean_pp_fields(pt)
                pt_copy['is_internal_transfer'] = False
                new_txns.append(pt_copy)

            elif not is_credit and 'PAYPAL' in bank_m:
                # PayPal 支出 → 银行 PAYPAL SEPA 扣款
                # 用 PayPal 真实商户名覆盖银行条目
                bt['merchant'] = pp_merchant
                bt['details'] = bt.get('details', '') + '\n[PayPal] ' + pt.get('details', '')
                bt['_paypal_enhanced'] = True

            elif not is_credit and pp_merchant.upper() in bank_m:
                # 同一商户已在银行数据中（如 McDonald's 直接从银行扣款）
                pass

            elif is_credit and pp_merchant.upper() in bank_m:
                # 同一收款人直接向银行打款
                pass

            else:
                # 匹配到非 PayPal 银行交易 → 作为新交易加入
                new_txns.append(_clean_pp_fields(pt))
        else:
            # 未匹配 → 作为新交易加入
            # ponytail: 不再做 Balance 二次匹配。新 CSV 的 Balance 是累计余额，
            # 不是银行扣款金额，无法用于匹配银行 PAYPAL 条目。
            new_txns.append(_clean_pp_fields(pt))

    return new_txns, matched_bank_indices


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
    upper = re.sub(r'\s+', ' ', merchant.upper()).strip()
    if amount > 0:
        # 收入：匹配 INCOME_RULES
        for cat, keywords in INCOME_RULES:
            for kw in keywords:
                if kw in upper or kw in details.upper():
                    return cat
        return "其他收入"
    # 支出：匹配 EXPENSE_RULES
    for cat, keywords in EXPENSE_RULES:
        for kw in keywords:
            if kw in upper:
                return cat
    # PayPal 未识别 → 尝试从详情匹配关键词，仍不匹配则归 PayPal通用
    if 'PAYPAL' in upper:
        du = details.upper()
        for cat, keywords in EXPENSE_RULES:
            for kw in keywords:
                if kw in du:
                    return cat
        return "PayPal通用"
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
        csv_files = list(PDF_DIR.glob("*.csv"))
        cache_count = raw.get("_count", 0)
        if len(pdf_files) != cache_count:
            return []
        for pdf in pdf_files:
            if pdf.stat().st_mtime > cache_mtime:
                return []  # 有新 PDF 或被修改，缓存失效
        for csv_f in csv_files:
            if csv_f.stat().st_mtime > cache_mtime:
                return []  # CSV (PayPal/TR) 更新 → 缓存失效
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
        if t.get('is_internal_transfer') or t.get('is_failed_transaction'):
            continue  # 内部转账、换汇、失败交易不参与图表统计
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
        yaxis_tickprefix='€', yaxis_tickformat=',.0f',
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
        yaxis_tickprefix='€', yaxis_tickformat=',.0f',
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
        yaxis_tickprefix='€', yaxis_tickformat=',.0f',
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
            yaxis_tickprefix='€', yaxis_tickformat=',.0f',
        )
        yearly_charts[yr] = fig

    # ── JSON 数据嵌入（供前端 JS 筛选使用）──
    import json as _json
    txns_json = _json.dumps(txns, ensure_ascii=False, default=str)

    # ── 统计卡片（排除内部转账）──
    ext_txns = [t for t in txns if not t.get('is_internal_transfer') and not t.get('is_failed_transaction')]
    total_in = sum(t['amount'] for t in ext_txns if t['amount'] > 0)
    total_out = abs(sum(t['amount'] for t in ext_txns if t['amount'] < 0))
    net = total_in - total_out
    date_range = f"{txns[0]['booking_date']} ~ {txns[-1]['booking_date']}"
    avg_monthly = total_out / len(month_keys) if month_keys else 0
    total_count = len(txns)

    # ── 交易明细表行 ──
    table_rows = []
    accounts = sorted(set(t.get('account', 'DB') for t in txns))
    txn_idx = 0  # 用于 JS 端 data-idx 定位
    for t in reversed(txns):
        if t.get('is_failed_transaction'):
            continue  # 失败交易不出现在交易明细中
        css = 'inc' if t['amount'] > 0 else 'exp'
        if t.get('is_internal_transfer'):
            css += ' internal'
        cat = t.get('category', '其他')
        acct = t.get('account', 'DB')
        merchant_attr = t.get("merchant","")[:55].replace('"', '&quot;')
        table_rows.append(
            f'<tr class="{css}" data-category="{cat}" data-account="{acct}" data-internal="{1 if t.get("is_internal_transfer") else 0}" data-failed="{1 if t.get("is_failed_transaction") else 0}" data-date="{t["booking_date"]}" data-idx="{txn_idx}" data-merchant="{merchant_attr}">'
            f'<td>{t["booking_date"]}</td>'
            f'<td>{t.get("merchant","")[:55]}</td>'
            f'<td><span class="tag cat-editable" title="点击修改分类">{cat}</span></td>'
            f'<td class="amt" data-amount="{t["amount"]}">{t["amount"]:+,.2f}</td>'
            f'</tr>'
        )
        txn_idx += 1

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

/* Pie chart back button */
.pie-back-btn{{
  display:inline-block;margin-bottom:8px;padding:4px 12px;
  background:var(--surface);color:var(--accent);border:1px solid var(--border);
  border-radius:6px;cursor:pointer;font-size:.8rem;transition:background .15s;
}}
.pie-back-btn:hover{{background:var(--accent);color:#fff;border-color:var(--accent)}}

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
tr.failed td{{color:#ef4444;text-decoration:line-through}}
tr:hover td{{background:#f8fafc}}
.amt{{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}}
tr.inc .amt{{color:var(--green)}}tr.exp .amt{{color:var(--red)}}
.tag{{
  display:inline-block;padding:2px 10px;border-radius:12px;font-size:.76rem;
  background:#f1f5f9;color:var(--text2);font-weight:500;
}}
.tag.cat-editable{{
  cursor:pointer;transition:background .15s,color .15s;
}}
.tag.cat-editable:hover{{
  background:var(--accent);color:#fff;
}}
.cat-edit-select{{
  padding:2px 4px;font-size:.76rem;border-radius:4px;
  border:1px solid var(--accent);background:var(--surface);color:var(--text);
  max-width:110px;cursor:pointer;
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
<button class="active" data-tab="tab-report">月度报表</button>
<button data-tab="tab-charts">图表</button>
<button data-tab="tab-yearly">年度</button>
<button data-tab="tab-settings">设置</button>
</nav>

<!-- 数据筛选（全页面共享）-->
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
{"".join(f'<option value="{m}" data-year="{m[:4]}">{m}</option>' for m in month_keys)}
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

<div id="tab-report" class="tab-content active-tab">

<!-- 月度 KPI -->
<div class="kpi" id="report-kpi">
<div class="kpi-in"><div class="lbl">总收入</div><div class="val in" id="rpt-income">€{total_in:,.2f}</div></div>
<div class="kpi-out"><div class="lbl">总支出</div><div class="val out" id="rpt-expense">€{total_out:,.2f}</div></div>
<div class="kpi-net"><div class="lbl">净额</div><div class="val net" id="rpt-balance">€{net:+,.2f}</div></div>
<div class="kpi-count"><div class="lbl">交易笔数</div><div class="val" id="rpt-count">{len(txns)}</div></div>
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

<!-- 饼图（JS 动态渲染，跟随筛选联动）-->
<div class="charts-grid">
<div class="card"><h2>支出分类占比</h2><button id="rpt-expense-pie-back" class="pie-back-btn" style="display:none" title="返回上一级">&larr; 返回上一级</button><div id="rpt-expense-pie" style="height:440px"></div></div>
<div class="card"><h2>收入分类占比</h2><button id="rpt-income-pie-back" class="pie-back-btn" style="display:none" title="返回上一级">&larr; 返回上一级</button><div id="rpt-income-pie" style="height:440px"></div></div>
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
<tbody id="txn-tbody">{"".join(table_rows)}</tbody>
</table></div>
</div>

</div><!-- /tab-report -->

<div id="tab-charts" class="tab-content">

<div class="card"><h2>月度支出分类</h2><div id="chart-monthly-cat" style="height:480px"></div></div>

<div class="charts-grid">
<div class="card"><h2>月度收支对比</h2><div id="chart-monthly-compare" style="height:400px"></div></div>
<div class="card"><h2>累计净额走势</h2><div id="chart-cumulative-net" style="height:360px"></div></div>
</div>

</div><!-- /tab-charts -->
</div><!-- /tab-charts -->

<div id="tab-yearly" class="tab-content">
<!-- 年度趋势图（JS 动态渲染） -->
<div class="card" style="margin-top:20px"><h2>年度收支趋势</h2>
<div id="yearly-trend-chart" style="height:400px"></div>
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


</div><!-- /tab-yearly -->

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

<h2 style="margin-top:24px">分类审核：待定规则</h2>
<p style="font-size:.8rem;color:var(--text2);margin-bottom:12px">在交易表中点击分类标签修改后自动记录。导出后可用于 --apply-overrides 固化。</p>
<div id="bank-pending-rules" class="cat-list"></div>

<h2 style="margin-top:24px">数据导出</h2>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:8px">
<button id="export-overrides" style="padding:10px 20px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.9rem">
<i class="fas fa-file-export"></i> 导出分类覆盖
</button>
<button id="export-json" style="padding:10px 20px;background:var(--green);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:.9rem">
<i class="fas fa-download"></i> 导出全部 JSON
</button>
</div>
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
    isFailed: t.is_failed_transaction || false,
  }};
}});

/* ── Category hierarchy ── */
var CAT_HIERARCHY = {{
  '固定支出': {{
    subs: ['房租','电费','广电费','网费','话费','健康保险','第三方责任险','其他保险',
           {{name:'汽车/交通', subs:['车险','汽车保养','油费','停车费','汽车税','罚款']}},
           '健身']
  }},
  '活动支出': {{
    subs: [
      {{name:'线下购物', subs:['家居用品','超市日用品']}}, '线上购物','餐饮外食',
      '公共交通',{{name:'宠物', subs:['宠物保险','宠物食品','兽医']}},'医疗','旅行','服饰','娱乐','学费','市政缴费','网上充值','邮寄',
      '家人转账','朋友转账','投资','PayPal通用','其他'
    ]
  }},
  '收入': {{
    subs: ['工资','大学薪资','奖学金/津贴','二手收入','投资收入','利息收入','朋友转账','家人转账','退款','其他收入']
  }}
}};

function getSubCats(subs) {{
  var result = [];
  subs.forEach(function(s) {{
    if (typeof s === 'string') result.push(s);
    else {{ result.push(s.name); result = result.concat(getSubCats(s.subs)); }}
  }});
  return result;
}}


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

var tbody=document.getElementById('txn-tbody');
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

/* ── Category inline edit ── */
var catEditOpen = null;
function closeCatEdit() {{
  if (catEditOpen) {{
    var sel = catEditOpen.sel;
    var origTag = document.createElement('span');
    origTag.className = 'tag cat-editable';
    origTag.title = '点击修改分类';
    origTag.textContent = catEditOpen.origCat;
    sel.replaceWith(origTag);
    catEditOpen = null;
  }}
}}

tbody.addEventListener('click', function(e) {{
  var tag = e.target.closest('.cat-editable');
  if (!tag) {{ closeCatEdit(); return; }}
  if (catEditOpen && catEditOpen.tag === tag) return; // already editing this one
  closeCatEdit();

  var row = tag.closest('tr');
  var idx = parseInt(row.dataset.idx);
  if (isNaN(idx) || idx < 0 || idx >= transactions.length) return;
  var txn = transactions[idx];
  var allCats = txn.type === 'expense' ? expCats : incCats;

  var sel = document.createElement('select');
  sel.className = 'cat-edit-select';
  sel.style.cssText = 'padding:2px 4px;font-size:.78rem;border-radius:4px;border:1px solid var(--accent);background:var(--surface);color:var(--text);max-width:110px';
  allCats.forEach(function(c) {{
    var opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    if (c === txn.category) opt.selected = true;
    sel.appendChild(opt);
  }});

  tag.replaceWith(sel);
  catEditOpen = {{ tag: tag, sel: sel, origCat: txn.category, row: row, idx: idx }};
  sel.focus();

  sel.addEventListener('change', function() {{
    var newCat = sel.value;
    if (newCat === catEditOpen.origCat) {{ closeCatEdit(); return; }}
    txn.category = newCat;
    row.dataset.category = newCat;
    var merchant = txn.merchant.toUpperCase();
    if (merchant) {{
      categoryOverrides[merchant] = newCat;
      pendingRules[merchant] = newCat;
      saveOverrides();
    }}
    // Replace select with new tag
    var newTag = document.createElement('span');
    newTag.className = 'tag cat-editable';
    newTag.title = '点击修改分类';
    newTag.textContent = newCat;
    sel.replaceWith(newTag);
    catEditOpen = null;
    // Refresh all views
    updateReport();
    applyFilters();
    updateAllCharts();
    renderPendingRules();
    notify('分类已更新: ' + txn.merchant + ' → ' + newCat);
  }});

  sel.addEventListener('blur', function() {{
    setTimeout(function() {{
      if (catEditOpen && catEditOpen.sel === sel) closeCatEdit();
    }}, 200);
  }});

  sel.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') {{ closeCatEdit(); }}
  }});
}});

/* ── Combined filter ── */
function applyFilters(){{
  var q=searchInput.value.toLowerCase().trim();
  var account = document.getElementById('report-account').value;
  var year = document.getElementById('report-year').value;
  var month = document.getElementById('report-month').value;
  var amtMin = parseFloat(document.getElementById('report-amt-min').value) || 0;
  var amtMax = parseFloat(document.getElementById('report-amt-max').value) || Infinity;
  var visible=0;
  rows.forEach(function(r){{
    var matchAcct=account==='all'||r.dataset.account===account;
    var matchYear=year==='all'||(r.dataset.date||'').substring(0,4)===year;
    var matchMonth=month==='all'||(r.dataset.date||'').substring(0,7)===month;
    var matchAmt=true;
    if(amtMin>0||amtMax<Infinity){{
      var amt=Math.abs(parseFloat((r.querySelector('.amt')||{{}}).dataset?.amount)||0);
      matchAmt=amt>=amtMin&&amt<=amtMax;
    }}
    var matchCat=!activeCat||r.dataset.category===activeCat;
    var matchSearch=!q||r.textContent.toLowerCase().indexOf(q)!==-1;
    var show=matchAcct&&matchYear&&matchMonth&&matchAmt&&matchCat&&matchSearch;
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
  var extFiltered = filtered.filter(function(t) {{ return !t.isInternal && !t.isFailed; }});
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

  // Hierarchical category detail table
  var totalExpense = totalOut;
  var tbody = document.getElementById('rpt-expense-detail');
  tbody.innerHTML = '';

  function renderLevel(container, subs, level) {{
    subs.forEach(function(sub) {{
      var subName = typeof sub === 'string' ? sub : sub.name;
      var subSubs = typeof sub === 'string' ? null : sub.subs;
      var amt = catTotals[subName] || 0;
      // 父分类显示子项合计
      if (subSubs) {{
        amt = 0;
        subSubs.forEach(function(ss) {{ amt += (catTotals[ss] || 0); }});
      }}
      if (amt === 0 && !subSubs) return;
      var pct = totalExpense > 0 ? (amt / totalExpense * 100).toFixed(1) : '0.0';
      var cnt = catCounts[subName] || 0;
      var indent = 'padding-left:' + (16 + level*20) + 'px';

      var tr = document.createElement('tr');
      tr.className = 'cat-expand-row';
      tr.style.cursor = subSubs ? 'pointer' : 'default';
      var icon = subSubs ? '<i class=\"fas fa-chevron-right\" style=\"font-size:.7rem;margin-right:6px;transition:transform .2s\"></i>' : '<span style=\"display:inline-block;width:16px\"></span>';
      tr.innerHTML = '<td style=\"' + indent + '\">' + icon + subName + '</td>' +
        '<td style=\"text-align:right;color:var(--red);font-weight:600\">' + (amt > 0 ? 'EUR ' + amt.toFixed(2) : '-') + '</td>' +
        '<td style=\"text-align:right\">' + (amt > 0 ? pct + '%' : '-') + '</td>' +
        '<td style=\"text-align:right\">' + (cnt > 0 ? cnt : '-') + '</td>';
      container.appendChild(tr);

      if (subSubs) {{
        subSubs.forEach(function(ss) {{
          var ssAmt = catTotals[ss] || 0;
          if (ssAmt === 0) return;
          var ssPct = totalExpense > 0 ? (ssAmt / totalExpense * 100).toFixed(1) : '0.0';
          var ssCnt = catCounts[ss] || 0;
          var ssRow = document.createElement('tr');
          ssRow.className = 'cat-l3-row';
          ssRow.style.display = 'none';
          ssRow.innerHTML = '<td style=\"padding-left:' + (36 + level*20) + 'px;font-size:.85rem;color:var(--text2)\">' + ss + '</td>' +
            '<td style=\"text-align:right;color:var(--red);font-size:.85rem\">EUR ' + ssAmt.toFixed(2) + '</td>' +
            '<td style=\"text-align:right;font-size:.85rem\">' + ssPct + '%</td>' +
            '<td style=\"text-align:right;font-size:.85rem\">' + ssCnt + '</td>';
          container.appendChild(ssRow);
        }});

        tr.addEventListener('click', function() {{
          var icon = this.querySelector('i');
          var next = this.nextElementSibling;
          while (next && next.classList.contains('cat-l3-row')) {{
            var show = next.style.display === 'none';
            next.style.display = show ? '' : 'none';
            next = next.nextElementSibling;
          }}
          icon.style.transform = icon.style.transform === 'rotate(90deg)' ? '' : 'rotate(90deg)';
        }});
      }}
    }});
  }}

  Object.keys(CAT_HIERARCHY).forEach(function(l1) {{
    var info = CAT_HIERARCHY[l1];
    var l1Subs = info.subs;
    var l1Total = 0, l1Cnt = 0;
    getSubCats(l1Subs).forEach(function(sc) {{
      l1Total += (catTotals[sc] || 0);
      l1Cnt += (catCounts[sc] || 0);
    }});
    if (l1Total === 0) return;

    var tr = document.createElement('tr');
    tr.className = 'cat-l1-row';
    tr.style.cursor = 'pointer';
    tr.style.cssText = 'background:var(--bg);font-weight:700;border-top:2px solid var(--border)';
    tr.innerHTML = '<td><i class=\"fas fa-chevron-right\" style=\"font-size:.7rem;margin-right:6px;transition:transform .2s\"></i>' + l1 + '</td>' +
      '<td style=\"text-align:right;color:var(--red);font-weight:700\">EUR ' + l1Total.toFixed(2) + '</td>' +
      '<td style=\"text-align:right\">' + (totalExpense > 0 ? (l1Total/totalExpense*100).toFixed(1) : '0') + '%</td>' +
      '<td style=\"text-align:right\">' + l1Cnt + '</td>';
    tbody.appendChild(tr);

    var l2Container = document.createElement('tbody');
    l2Container.style.display = 'none';
    renderLevel(l2Container, l1Subs, 0);
    tbody.appendChild(l2Container);

    tr.addEventListener('click', function() {{
      var icon = this.querySelector('i');
      var next = this.nextElementSibling;
      var show = next.style.display === 'none';
      next.style.display = show ? '' : 'none';
      icon.style.transform = show ? 'rotate(90deg)' : 'rotate(0deg)';
    }});
  }});
updatePieChart('rpt-expense-pie', 'expense', extFiltered);
  updatePieChart('rpt-income-pie', 'income', extFiltered);
}}


/* ── Yearly trend chart (dynamic) ── */
function updateYearlyTrendChart(txns) {{
  var months = ['01月','02月','03月','04月','05月','06月','07月','08月','09月','10月','11月','12月'];
  var incData = Array(12).fill(0), expData = Array(12).fill(0);
  txns.forEach(function(t) {{
    var m = parseInt(t.date.substring(5,7)) - 1;
    if (m >= 0 && m < 12) {{
      if (t.type === 'income') incData[m] += t.amount; else expData[m] += t.amount;
    }}
  }});
  var balData = incData.map(function(v,i) {{ return v - expData[i]; }});
  var data = [
    {{ type: 'bar', name: '收入', x: months, y: incData, marker: {{color: '#10b981'}} }},
    {{ type: 'bar', name: '支出', x: months, y: expData, marker: {{color: '#ef4444'}} }},
    {{ type: 'scatter', name: '结余', x: months, y: balData, mode: 'lines+markers',
       line: {{color: '#6366f1', width: 3}}, marker: {{size: 8}} }}
  ];
  var layout = {{
    barmode: 'group', height: 400, hovermode: 'x unified',
    template: 'plotly_white', margin: {{l:50,r:10,t:30,b:60}},
    legend: {{orientation:'h',y:-0.15,x:0.5,xanchor:'center'}},
    yaxis: {{tickprefix: 'EUR ', tickformat: ',.0f'}},
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  }};
  Plotly.react('yearly-trend-chart', data, layout, {{displayModeBar: false, responsive: true}});
}}

/* ── Charts tab: monthly category stacked bar ── */
function updateMonthlyCategoryChart(txns) {{
  var months = [], catMap = {{}};
  txns.filter(function(t) {{ return t.type === 'expense'; }}).forEach(function(t) {{
    var m = t.date.substring(0,7);
    if (months.indexOf(m) < 0) months.push(m);
    if (!catMap[t.category]) catMap[t.category] = {{}};
    catMap[t.category][m] = (catMap[t.category][m] || 0) + t.amount;
  }});
  months.sort();
  var traces = [];
  var colors = ['#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#10b981','#14b8a6','#06b6d4','#3b82f6','#6366f1','#8b5cf6','#a855f7','#d946ef','#ec4899'];
  var ci = 0;
  Object.keys(catMap).sort(function(a,b) {{
    var ta=0,tb=0; Object.values(catMap[a]).forEach(function(v){{ta+=v}}); Object.values(catMap[b]).forEach(function(v){{tb+=v}});
    return tb-ta;
  }}).forEach(function(cat) {{
    traces.push({{ type: 'bar', name: cat, x: months, y: months.map(function(m){{return catMap[cat][m]||0}}), marker:{{color:colors[ci%colors.length]}} }});
    ci++;
  }});
  var layout = {{ barmode:'stack', height:420, template:'plotly_white', margin:{{l:40,r:10,t:30,b:60}},
    legend:{{orientation:'h',y:-0.15,x:0.5,xanchor:'center'}},
    yaxis:{{tickprefix:'EUR ',tickformat:',.0f'}}, paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)' }};
  Plotly.react('chart-monthly-cat', traces, layout, {{displayModeBar:false,responsive:true}});
}}

/* ── Charts tab: monthly income vs expense ── */
function updateMonthlyCompareChart(txns) {{
  var months = [], incMap={{}}, expMap={{}};
  txns.forEach(function(t) {{
    var m = t.date.substring(0,7);
    if (months.indexOf(m) < 0) months.push(m);
    if (t.type === 'income') incMap[m]=(incMap[m]||0)+t.amount; else expMap[m]=(expMap[m]||0)+t.amount;
  }});
  months.sort();
  var data = [
    {{ type:'bar',name:'收入',x:months,y:months.map(function(m){{return incMap[m]||0}}),marker:{{color:'#10b981'}}}},
    {{ type:'bar',name:'支出',x:months,y:months.map(function(m){{return expMap[m]||0}}),marker:{{color:'#ef4444'}}}}
  ];
  var layout = {{ barmode:'group',height:360,template:'plotly_white',margin:{{l:40,r:10,t:30,b:60}},
    legend:{{orientation:'h',y:-0.15,x:0.5,xanchor:'center'}},
    yaxis:{{tickprefix:'EUR ',tickformat:',.0f'}},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)' }};
  Plotly.react('chart-monthly-compare', data, layout, {{displayModeBar:false,responsive:true}});
}}

/* ── Charts tab: cumulative net ── */
function updateCumulativeNetChart(txns) {{
  var sorted = txns.slice().sort(function(a,b){{return a.date.localeCompare(b.date)}});
  var dates=[], running=0, run=[];
  sorted.forEach(function(t) {{
    dates.push(t.date);
    running += (t.type==='income'? t.amount : -t.amount);
    run.push(running);
  }});
  var data = [{{ type:'scatter',x:dates,y:run,mode:'lines',fill:'tozeroy',name:'累计净额',line:{{color:'#6366f1',width:2}} }}];
  var layout = {{ height:360,template:'plotly_white',margin:{{l:50,r:10,t:30,b:40}},
    yaxis:{{tickprefix:'EUR ',tickformat:',.0f'}},paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)' }};
  Plotly.react('chart-cumulative-net', data, layout, {{displayModeBar:false,responsive:true}});
}}

/* ── Master chart updater ── */
function updateAllCharts(txns) {{
  updateYearlyTrendChart(txns);
  updateMonthlyCategoryChart(txns);
  updateMonthlyCompareChart(txns);
  updateCumulativeNetChart(txns);
}}
function updatePieChart(divId, type, txns) {{
  // Aggregate by category first
  var catData = {{}};
  txns.filter(function(t) {{ return t.type === type; }}).forEach(function(t) {{
    catData[t.category] = (catData[t.category] || 0) + t.amount;
  }});

  // L1 aggregation
  var l1Data = {{}};
  Object.keys(CAT_HIERARCHY).forEach(function(l1) {{
    var total = 0;
    getSubCats(CAT_HIERARCHY[l1].subs).forEach(function(sc) {{
      total += (catData[sc] || 0);
    }});
    if (total > 0) l1Data[l1] = total;
  }});

  var div = document.getElementById(divId);
  // Store data for drill-down
  div._catData = catData;
  div._l1Data = l1Data;
  div._type = type;
  div._drillLevel = 0;
  div._drillParent = null;

  renderPieLevel(divId, 0, null);

  // Wire back button
  var backBtn = document.getElementById(divId + '-back');
  if (backBtn) {{
    backBtn.onclick = function() {{
      var plotDiv = document.getElementById(divId);
      plotDiv._drillLevel = 0;
      plotDiv._drillParent = null;
      renderPieLevel(divId, 0, null);
    }};
  }}
}}

function renderPieLevel(divId, level, parentCat) {{
  var div = document.getElementById(divId);
  var backBtn = document.getElementById(divId + '-back');
  if (backBtn) {{
    backBtn.style.display = level > 0 ? 'inline-block' : 'none';
  }}
  var catData = div._catData;
  var l1Data = div._l1Data;
  var type = div._type;
  var colors = type === 'expense'
    ? ['#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#10b981','#14b8a6','#06b6d4','#3b82f6','#6366f1','#8b5cf6','#a855f7','#d946ef','#ec4899']
    : ['#10b981','#22c55e','#84cc16','#14b8a6','#06b6d4','#3b82f6','#6366f1','#8b5cf6','#a855f7','#d946ef'];

  var labels, values, title;
  if (level === 0) {{
    // L1: 固定支出 / 活动支出 / 收入
    labels = Object.keys(l1Data);
    values = Object.values(l1Data);
    title = type === 'expense' ? '支出分类 (点击下钻)' : '收入分类 (点击下钻)';
  }} else if (level === 1) {{
    // L2: subcategories of parent
    var info = CAT_HIERARCHY[parentCat];
    if (!info) return;
    var subs = [];
    info.subs.forEach(function(s) {{
      if (typeof s === 'string') {{
        var v = catData[s] || 0;
        if (v > 0) subs.push({{label: s, value: v, hasSubs: false}});
      }} else {{
        var v = 0;
        getSubCats(s.subs).forEach(function(ss) {{ v += (catData[ss] || 0); }});
        if (v > 0) subs.push({{label: s.name, value: v, hasSubs: true}});
      }}
    }});
    subs.sort(function(a,b) {{ return b.value - a.value; }});
    labels = subs.map(function(s) {{ return s.label; }});
    values = subs.map(function(s) {{ return s.value; }});
    title = parentCat + ' (点击下钻)';
  }} else {{
    // L3: subcategories of L2
    var info3 = null;
    Object.keys(CAT_HIERARCHY).forEach(function(l1) {{
      CAT_HIERARCHY[l1].subs.forEach(function(s) {{
        if (typeof s !== 'string' && s.name === parentCat) info3 = s;
      }});
    }});
    if (!info3) return;
    var subs3 = [];
    info3.subs.forEach(function(ss) {{
      var v = catData[ss] || 0;
      if (v > 0) subs3.push({{label: ss, value: v}});
    }});
    labels = subs3.map(function(s) {{ return s.label; }});
    values = subs3.map(function(s) {{ return s.value; }});
    title = parentCat + ' 明细';
  }}

  var total = values.reduce(function(a,b) {{ return a+b; }}, 0);
  var data = [{{
    type: 'pie', labels: labels, values: values, hole: 0.45,
    textinfo: labels.length <= 8 ? 'label+percent' : 'percent',
    textposition: labels.length <= 8 ? 'auto' : 'outside',
    textfont: {{size: 11}},
    marker: {{colors: colors.slice(0, labels.length)}},
    automargin: true,
    hoverinfo: 'label+value+percent',
    hovertemplate: '%{{label}}<br>EUR %{{value:,.2f}} (%{{percent}})<extra></extra>'
  }}];
  var layout = {{
    height: 440, title: title, titlefont: {{size: 13}},
    margin: {{l: 20, r: 80, t: 50, b: 20}},
    showlegend: true,
    legend: {{orientation: 'v', y: 0.5, x: 1.05, xanchor: 'left'}},
    template: 'plotly_white',
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  }};
  var config = {{displayModeBar: false, responsive: true}};

  var plotDiv = document.getElementById(divId);
  Plotly.react(divId, data, layout, config).then(function() {{
    // Wire click for drill-down
    plotDiv.on('plotly_click', function(eventData) {{
      var clicked = eventData.points[0];
      if (!clicked) return;
      var label = clicked.label;
      var drillLevel = plotDiv._drillLevel;
      if (drillLevel === 0) {{
        // Drilled from L1 to L2
        plotDiv._drillLevel = 1;
        plotDiv._drillParent = label;
        renderPieLevel(divId, 1, label);
      }} else if (drillLevel === 1) {{
        // Check if this subcategory has further drill-down
        var hasSubs = false;
        Object.keys(CAT_HIERARCHY).forEach(function(l1) {{
          CAT_HIERARCHY[l1].subs.forEach(function(s) {{
            if (typeof s !== 'string' && s.name === label) hasSubs = true;
          }});
        }});
        if (hasSubs) {{
          plotDiv._drillLevel = 2;
          plotDiv._drillParent = label;
          renderPieLevel(divId, 2, label);
        }} else {{
          // Click leaf: back to L1
          plotDiv._drillLevel = 0;
          plotDiv._drillParent = null;
          renderPieLevel(divId, 0, null);
        }}
      }} else {{
        // L3 → back to L1
        plotDiv._drillLevel = 0;
        plotDiv._drillParent = null;
        renderPieLevel(divId, 0, null);
      }}
    }});
    // Double-click to go back
    plotDiv.on('plotly_doubleclick', function() {{
      if (plotDiv._drillLevel > 0) {{
        plotDiv._drillLevel = 0;
        plotDiv._drillParent = null;
        renderPieLevel(divId, 0, null);
      }}
    }});
  }});
}}
// Wire report filters
document.getElementById('report-account').addEventListener('change', function() {{
  updateReport();
  updateYearlyStats();
  applyFilters();
  // 获取排除后的数据更新图表
  var acct=this.value, yr=document.getElementById('report-year').value, mo=document.getElementById('report-month').value;
  var filtered=transactions.filter(function(t){{
    if(!t.isInternal&&!t.isFailed) {{
      if(acct!=='all'&&t.account!==acct) return false;
      if(yr!=='all'&&t.date.substring(0,4)!==yr) return false;
      if(mo!=='all'&&t.date.substring(0,7)!==mo) return false;
      return true;
    }}
    return false;
  }});
  updateAllCharts(filtered);
}});
document.getElementById('report-year').addEventListener('change', function() {{
  var yr = this.value;
  // 先筛选月份下拉
  var monthSel = document.getElementById('report-month');
  Array.from(monthSel.options).forEach(function(opt) {{
    if (opt.value === 'all') return;
    opt.style.display = (yr === 'all' || opt.dataset.year === yr) ? '' : 'none';
  }});
  // 先重置月份再更新报表
  if (yr !== 'all' && monthSel.value !== 'all' && monthSel.value.substring(0,4) !== yr) {{
    monthSel.value = 'all';
  }}
  updateReport();
  updateYearlyStats();
  // 更新动态图表
  var acct2=document.getElementById('report-account').value, mo2=document.getElementById('report-month').value;
  var filtered2=transactions.filter(function(t){{
    if(!t.isInternal&&!t.isFailed) {{
      if(acct2!=='all'&&t.account!==acct2) return false;
      if(yr!=='all'&&t.date.substring(0,4)!==yr) return false;
      if(mo2!=='all'&&t.date.substring(0,7)!==mo2) return false;
      return true;
    }}
    return false;
  }});
  updateAllCharts(filtered2);
}});
document.getElementById('report-month').addEventListener('change', function() {{ updateReport(); applyFilters(); updateAllCharts(getFilteredExtTxns()); }});
document.getElementById('report-category').addEventListener('change', function() {{ updateReport(); applyFilters(); }});
document.getElementById('report-search').addEventListener('input', function() {{
  clearTimeout(this._timer);
  this._timer = setTimeout(function() {{ updateReport(); applyFilters(); }}, 200);
}});
document.getElementById('report-amt-min').addEventListener('input', function() {{
  clearTimeout(this._timer2);
  this._timer2 = setTimeout(function() {{ updateReport(); applyFilters(); }}, 300);
}});
document.getElementById('report-amt-max').addEventListener('input', function() {{
  clearTimeout(this._timer3);
  this._timer3 = setTimeout(function() {{ updateReport(); applyFilters(); }}, 300);
}});

// Initial report render
updateReport();
updateAllCharts(getFilteredExtTxns());

function getFilteredExtTxns() {{
  var acct=document.getElementById('report-account').value;
  var yr=document.getElementById('report-year').value;
  var mo=document.getElementById('report-month').value;
  return transactions.filter(function(t){{
    if(t.isInternal||t.isFailed) return false;
    if(acct!=='all'&&t.account!==acct) return false;
    if(yr!=='all'&&t.date.substring(0,4)!==yr) return false;
    if(mo!=='all'&&t.date.substring(0,7)!==mo) return false;
    return true;
  }});
}}


/* ── Yearly statistics ── */
function updateYearlyStats() {{
  var account = document.getElementById('report-account').value;
  var selectedYear = document.getElementById('report-year').value;
  if (selectedYear === 'all') selectedYear = String(new Date().getFullYear());
  var yrTxns = transactions.filter(function(t) {{
    if (t.isInternal || t.isFailed) return false;
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
var categoryOverrides = prefs.categoryOverrides || {{}};
var pendingRules = prefs.pendingRules || {{}};

// Apply stored category overrides to transactions
Object.keys(categoryOverrides).forEach(function(merchant) {{
  var mUpper = merchant.toUpperCase();
  transactions.forEach(function(t) {{
    if (t.merchant.toUpperCase() === mUpper) {{
      t.category = categoryOverrides[merchant];
    }}
  }});
}});
// Update DOM rows to match overridden categories
rows.forEach(function(r) {{
  var idx = parseInt(r.dataset.idx);
  if (idx >= 0 && idx < transactions.length) {{
    var cat = transactions[idx].category;
    r.dataset.category = cat;
    var tag = r.querySelector('.tag');
    if (tag) tag.textContent = cat;
  }}
}});

function saveOverrides() {{
  prefs.categoryOverrides = categoryOverrides;
  prefs.pendingRules = pendingRules;
  savePrefs(prefs);
}}

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
  renderPendingRules();
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

/* ── Export category overrides ── */
document.getElementById('export-overrides').addEventListener('click', function() {{
  var data = {{categoryOverrides: categoryOverrides, pendingRules: pendingRules}};
  var blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'category_overrides.json';
  a.click();
  notify('分类覆盖已导出: category_overrides.json');
}});

/* ── Pending rules management ── */
function renderPendingRules() {{
  var container = document.getElementById('bank-pending-rules');
  if (!container) return;
  var keys = Object.keys(pendingRules);
  if (keys.length === 0) {{
    container.innerHTML = '<div style="padding:8px;color:var(--text2);font-size:.85rem">暂无待定规则。在交易表中点击分类标签修改后自动生成。</div>';
    return;
  }}
  container.innerHTML = '';
  keys.forEach(function(merchant) {{
    var cat = pendingRules[merchant];
    var div = document.createElement('div');
    div.className = 'cat-item';
    div.innerHTML = '<span><strong>' + merchant + '</strong> → ' + cat + '</span>' +
      '<button class="del-btn" data-merchant="' + merchant + '"><i class="fas fa-trash"></i></button>';
    container.appendChild(div);
  }});
}}

document.getElementById('bank-pending-rules').addEventListener('click', function(e) {{
  var btn = e.target.closest('.del-btn');
  if (!btn) return;
  var merchant = btn.dataset.merchant;
  delete pendingRules[merchant];
  saveOverrides();
  renderPendingRules();
  notify('规则已删除: ' + merchant);
}});

/* ── Persist & notify helpers ── */
function persistAndRender() {{
  prefs.expCats = expCats;
  prefs.incCats = incCats;
  prefs.descTags = descTags;
  prefs.fixedRules = fixedRules;
  prefs.categoryOverrides = categoryOverrides;
  prefs.pendingRules = pendingRules;
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

        # 换汇交易: PAYM.ORDER 开头 → 标记为不计入收支
        if merchant.startswith('PAYM.ORDER'):
            t['is_internal_transfer'] = True
            # 从描述中提取真实汇款人
            if 'CHEN ZEJUN' in merchant.upper():
                t['merchant'] = '换汇 (Zejun Chen)'
            elif 'CHENG RUI' in merchant.upper():
                t['merchant'] = '换汇 (Cheng Rui)'
            else:
                t['merchant'] = '换汇'

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

    # 检测失败/冲正交易: ±3 天内同一金额同一人一进一出
    from collections import defaultdict as _dd
    from datetime import timedelta as _td
    by_amt_merchant = _dd(list)
    for t in deduped:
        if t.get('is_internal_transfer') or t.get('is_failed_transaction'):
            continue
        key = (round(abs(t['amount']), 2), t['merchant'][:20])
        by_amt_merchant[key].append(t)

    for key, group in by_amt_merchant.items():
        if len(group) < 2:
            continue
        # 排序后检查 ±3 天内是否有反向交易
        group.sort(key=lambda x: x['booking_date'])
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                # 必须方向相反
                if (a['amount'] > 0) == (b['amount'] > 0):
                    continue
                # ±3 天内
                try:
                    da = datetime.strptime(a['booking_date'], '%Y-%m-%d')
                    db = datetime.strptime(b['booking_date'], '%Y-%m-%d')
                except ValueError:
                    continue
                if abs((db - da).days) <= 3:
                    a['is_failed_transaction'] = True
                    b['is_failed_transaction'] = True

    return deduped


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="银行流水 PDF → 可视化 HTML 报告")
    parser.add_argument('--force', action='store_true', help='强制重新解析所有 PDF')
    parser.add_argument('--output', type=str, default=None, help='输出 HTML 路径')
    parser.add_argument('--month', type=str, default=None, help='仅输出指定月份 (YYYY-MM)')
    parser.add_argument('--apply-overrides', type=str, default=None, help='应用分类覆盖 JSON 文件（从 HTML 设置页签导出）')
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

        # 解析 Trade Republic CSV（跳过 PayPal CSV）
        for csv_path in sorted(PDF_DIR.glob("*.csv")):
            if csv_path.name.lower().startswith('paypal'):
                continue  # PayPal CSV 由专门解析器处理
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

        # 解析 PayPal CSV
        for csv_path in sorted(PDF_DIR.glob("Paypal*.csv")):
            print(f"-> 解析 {csv_path.name} (PayPal)...")
            pp_txns = parse_paypal_csv(csv_path)
            print(f"  -> 提取 {len(pp_txns)} 笔真实交易")
            pp_new, pp_matched = match_paypal_to_bank(pp_txns, all_txns)
            print(f"  -> 匹配 {len(pp_matched)} 笔银行交易, 新增 {len(pp_new)} 笔")
            for t in pp_new:
                t['category'] = categorize(t.get('merchant', ''), t['amount'], t.get('details', ''))
            all_txns.extend(pp_new)

        # PayPal 增强后重新分类被更新的银行交易
        for t in all_txns:
            if t.get('_paypal_enhanced'):
                t['category'] = categorize(t.get('merchant', ''), t['amount'], t.get('details', ''))
                del t['_paypal_enhanced']

        # 后处理：清洗商户名 + 去重
        all_txns = post_process(all_txns)

        # 分类（新交易和未分类的）
        for t in all_txns:
            if not t.get('category'):
                t['category'] = categorize(t.get('merchant', ''), t['amount'], t.get('details', ''))

        # 后处理分类修正：换汇 / 内部转账
        for t in all_txns:
            if t.get('is_internal_transfer'):
                if '换汇' in t.get('merchant', ''):
                    t['category'] = '换汇'
                else:
                    t['category'] = '内部转账'

        # 退款检测：类型信号 + 支出商户正向金额
        for t in all_txns:
            if t.get('category') != '其他收入' or t['amount'] <= 0:
                continue
            txn_type = t.get('type', '')
            merchant_upper = t.get('merchant', '').upper()
            # 信号1: 类型明确是退款
            if 'Rückgabe' in txn_type or txn_type == 'Payment Refund':
                t['category'] = '退款'
                continue
            # 信号2: PayPal Europe 正向 SEPA → 退款
            if 'PAYPAL EUROPE' in merchant_upper:
                t['category'] = '退款'
                continue
            # 信号3: 金额为正但商户匹配支出规则 → 退款
            for _cat, keywords in EXPENSE_RULES:
                for kw in keywords:
                    if kw in merchant_upper:
                        t['category'] = '退款'
                        break
                if t['category'] == '退款':
                    break

        # 存缓存
        save_cache(all_txns)
        internal_count = sum(1 for t in all_txns if t.get('is_internal_transfer'))
        print(f"[OK] 缓存已保存 ({len(all_txns)} 笔，其中 {internal_count} 笔内部转账)")

    # 应用分类覆盖（--apply-overrides）
    if args.apply_overrides:
        import json as _json2
        override_path = Path(args.apply_overrides)
        if not override_path.exists():
            print(f"[错误] 覆盖文件不存在: {override_path}")
            sys.exit(1)
        with open(override_path, 'r', encoding='utf-8') as f:
            overrides_data = _json2.load(f)
        overrides = overrides_data.get('categoryOverrides', {})
        applied = 0
        for t in all_txns:
            merchant_upper = t.get('merchant', '').upper()
            if merchant_upper in overrides:
                new_cat = overrides[merchant_upper]
                if t.get('category') != new_cat:
                    t['category'] = new_cat
                    applied += 1
        print(f"[OK] 分类覆盖已应用: {applied} 笔交易重新分类")

    # 按月份筛选
    if args.month:
        all_txns = [t for t in all_txns if t['booking_date'].startswith(args.month)]
        if not all_txns:
            print(f"[错误] {args.month} 没有交易数据")
            sys.exit(1)
        print(f"-> 筛选 {args.month}: {len(all_txns)} 笔")

    # 生成失败交易审计文件
    failed = [t for t in all_txns if t.get('is_failed_transaction')]
    if failed:
        audit_path = SCRIPT_DIR / "failed_transactions_audit.txt"
        lines = ["失败/冲正交易审计报告", "=" * 60,
                 f"共 {len(failed)} 笔（已从统计中排除）", ""]
        for t in sorted(failed, key=lambda x: x['booking_date']):
            lines.append(f"{t['booking_date']} | {t['amount']:>10.2f} | {t.get('merchant','')}")
            lines.append(f"  账户: {t.get('account','?')} | 类型: {t.get('type','?')}")
            if t.get('details'):
                lines.append(f"  详情: {t['details'][:100]}")
            lines.append("")
        lines.append("=" * 60)
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        audit_path.write_text('\n'.join(lines), encoding='utf-8')
        print(f"\n[审计] 失败交易报告: {audit_path} ({len(failed)} 笔)")

    # 生成报告
    output = Path(args.output) if args.output else OUTPUT_FILE
    html = build_report(all_txns)
    output.write_text(html, encoding='utf-8')
    total_in = sum(t['amount'] for t in all_txns if t['amount'] > 0)
    total_out = abs(sum(t['amount'] for t in all_txns if t['amount'] < 0))
    print(f"\n[OK] report generated: {output}")
    print(f"   income: EUR {total_in:,.2f}  expense: EUR {total_out:,.2f}  net: EUR {total_in-total_out:+,.2f}")

    # 生成简表
    simple_html = build_simple_report(all_txns)
    SIMPLE_OUTPUT.write_text(simple_html, encoding='utf-8')
    print(f"[OK] simple report: {SIMPLE_OUTPUT}")


# ═══════════════════════════════════════════════════════════════════════════════
# 简表：仅筛选 + KPI + 分类明细 + 交易明细
# ═══════════════════════════════════════════════════════════════════════════════

def build_simple_report(txns: list[dict]) -> str:
    """生成轻量财务简表 HTML（无图表、无页签）。"""
    ext_txns = [t for t in txns if not t.get('is_internal_transfer') and not t.get('is_failed_transaction')]
    ext_txns.sort(key=lambda t: t['booking_date'], reverse=True)

    txn_json = json.dumps([{
        'date': t['booking_date'],
        'type': 'income' if t['amount'] > 0 else 'expense',
        'category': t.get('category', '其他'),
        'amount': t['amount'],
        'merchant': t.get('merchant', ''),
        'account': t.get('account', 'DB'),
    } for t in ext_txns], ensure_ascii=False)

    years = sorted({t['booking_date'][:4] for t in ext_txns})
    categories = sorted({t.get('category', '其他') for t in ext_txns})

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>财务简表</title>
<style>
:root {{
  --bg: #f5f5f5; --card: #fff; --text: #222; --text2: #666; --border: #e0e0e0;
  --accent: #3b82f6; --income: #10b981; --expense: #ef4444;
}}
[data-theme="dark"] {{
  --bg: #1a1a2e; --card: #222238; --text: #eee; --text2: #999; --border: #333;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font:14px/1.5 system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); padding:16px; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ font-size:1.2rem; margin-bottom:12px; }}

/* Theme toggle */
.theme-btn {{ position:fixed; top:12px; right:12px; width:32px; height:32px; border-radius:50%; border:1px solid var(--border); background:var(--card); color:var(--text); cursor:pointer; font-size:16px; z-index:100; }}

/* Filters */
.filters {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; align-items:center; }}
.filters select, .filters input {{ padding:6px 10px; border:1px solid var(--border); border-radius:6px; font-size:.85rem; background:var(--card); color:var(--text); min-width:100px; }}
.filters input[type="number"] {{ width:100px; }}

/* KPI */
.kpi {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:16px; }}
.kpi-card {{ background:var(--card); border-radius:10px; padding:14px 18px; border:1px solid var(--border); }}
.kpi-card .label {{ font-size:.75rem; color:var(--text2); margin-bottom:4px; }}
.kpi-card .value {{ font-size:1.5rem; font-weight:700; }}
.kpi-card .value.income {{ color:var(--income); }}
.kpi-card .value.expense {{ color:var(--expense); }}

/* Tables */
.table-wrap {{ background:var(--card); border-radius:10px; border:1px solid var(--border); overflow:hidden; margin-bottom:16px; }}
.table-wrap h2 {{ font-size:.9rem; padding:12px 16px; border-bottom:1px solid var(--border); background:var(--bg); }}
table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
th {{ text-align:left; padding:8px 16px; border-bottom:1px solid var(--border); color:var(--text2); font-weight:600; cursor:pointer; white-space:nowrap; user-select:none; }}
th:hover {{ color:var(--accent); }}
td {{ padding:7px 16px; border-bottom:1px solid var(--border); }}
tr:last-child td {{ border-bottom:none; }}
.amount {{ text-align:right; font-variant-numeric:tabular-nums; }}
.income {{ color:var(--income); }}
.expense {{ color:var(--expense); }}
.pct-bar {{ display:inline-block; height:4px; border-radius:2px; margin-left:6px; vertical-align:middle; }}

/* pagination */
.pagination {{ display:flex; justify-content:center; gap:4px; padding:12px; }}
.pagination button {{ padding:4px 10px; border:1px solid var(--border); border-radius:4px; background:var(--card); color:var(--text); cursor:pointer; font-size:.8rem; }}
.pagination button.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
.pagination button:disabled {{ opacity:.4; cursor:default; }}

.info-bar {{ font-size:.8rem; color:var(--text2); padding:8px 16px; }}
</style>
</head>
<body>
<button class="theme-btn" onclick="toggleTheme()" title="切换主题">☀</button>
<div class="container">
<h1>财务简表</h1>

<div class="filters" id="filters">
  <select id="f-year"><option value="">全部年份</option></select>
  <select id="f-month"><option value="">全部月份</option></select>
  <select id="f-category"><option value="">全部分类</option></select>
  <input id="f-search" placeholder="搜索商户/描述…" type="search">
  <input id="f-min" type="number" step="0.01" placeholder="金额≥">
  <input id="f-max" type="number" step="0.01" placeholder="金额≤">
  <button onclick="resetFilters()" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);cursor:pointer;">重置</button>
</div>

<div class="kpi" id="kpi"></div>

<div class="table-wrap">
  <h2>分类明细</h2>
  <table><thead><tr>
    <th onclick="sortCat('name')">分类</th>
    <th onclick="sortCat('total')" style="text-align:right">金额</th>
    <th onclick="sortCat('pct')" style="text-align:right">占比</th>
    <th onclick="sortCat('count')" style="text-align:right">笔数</th>
  </tr></thead><tbody id="cat-body"></tbody></table>
</div>

<div class="table-wrap">
  <h2>交易明细 <span class="info-bar" id="txn-info"></span></h2>
  <table><thead><tr>
    <th onclick="sortTxn('date')">日期</th>
    <th onclick="sortTxn('merchant')">商户</th>
    <th onclick="sortTxn('category')">分类</th>
    <th onclick="sortTxn('amount')" style="text-align:right">金额</th>
  </tr></thead><tbody id="txn-body"></tbody></table>
  <div class="pagination" id="txn-pages"></div>
</div>
</div>

<script>
var DATA = {txn_json};
var PAGE_SIZE = 50;

// Init filters
(function() {{
  var years = [...new Set(DATA.map(function(t){{return t.date.slice(0,4)}}))].sort();
  var sel = document.getElementById('f-year');
  years.forEach(function(y){{ sel.appendChild(new Option(y,y)); }});
  var cats = [...new Set(DATA.map(function(t){{return t.category}}))].sort();
  var csel = document.getElementById('f-category');
  cats.forEach(function(c){{ csel.appendChild(new Option(c,c)); }});
  updateMonthOptions();
}})();

document.getElementById('f-year').addEventListener('change', updateMonthOptions);

function updateMonthOptions() {{
  var y = document.getElementById('f-year').value;
  var sel = document.getElementById('f-month');
  sel.innerHTML = '<option value="">全部月份</option>';
  if (!y) return;
  var months = [...new Set(DATA.filter(function(t){{return t.date.startsWith(y)}}).map(function(t){{return t.date.slice(5,7)}}))].sort();
  months.forEach(function(m){{ sel.appendChild(new Option(m+'月',m)); }});
}}

// Filter logic
var filtersEl = document.getElementById('filters');
filtersEl.addEventListener('input', debounce(renderAll, 200));
filtersEl.addEventListener('change', renderAll);

function getFiltered() {{
  var y = document.getElementById('f-year').value;
  var m = document.getElementById('f-month').value;
  var cat = document.getElementById('f-category').value;
  var q = document.getElementById('f-search').value.toLowerCase();
  var min = parseFloat(document.getElementById('f-min').value) || null;
  var max = parseFloat(document.getElementById('f-max').value) || null;

  return DATA.filter(function(t) {{
    if (y && !t.date.startsWith(y)) return false;
    if (m && t.date.slice(5,7) !== m) return false;
    if (cat && t.category !== cat) return false;
    if (q && !t.merchant.toLowerCase().includes(q)) return false;
    if (min !== null && Math.abs(t.amount) < min) return false;
    if (max !== null && Math.abs(t.amount) > max) return false;
    return true;
  }});
}}

// KPI
function renderKPI(filtered) {{
  var inc = filtered.filter(function(t){{return t.type==='income'}}).reduce(function(s,t){{return s+t.amount}},0);
  var exp = filtered.filter(function(t){{return t.type==='expense'}}).reduce(function(s,t){{return s+Math.abs(t.amount)}},0);
  document.getElementById('kpi').innerHTML =
    '<div class="kpi-card"><div class="label">总收入</div><div class="value income">€'+inc.toFixed(2)+'</div></div>' +
    '<div class="kpi-card"><div class="label">总支出</div><div class="value expense">€'+exp.toFixed(2)+'</div></div>' +
    '<div class="kpi-card"><div class="label">净额</div><div class="value" style="color:'+(inc>=exp?'var(--income)':'var(--expense)')+'">€'+(inc-exp).toFixed(2)+'</div></div>';
}}

// Category table
var catSort = {{key:'total', dir:-1}};
function sortCat(key) {{
  if (catSort.key === key) catSort.dir *= -1;
  else {{ catSort.key = key; catSort.dir = -1; }}
  renderAll();
}}

function renderCat(filtered) {{
  var map = {{}};
  filtered.forEach(function(t) {{
    var k = t.category;
    if (!map[k]) map[k] = {{total:0, count:0}};
    map[k].total += t.type==='expense' ? Math.abs(t.amount) : t.amount;
    map[k].count++;
  }});
  var rows = Object.entries(map);
  var grand = rows.reduce(function(s,r){{return s+Math.abs(r[1].total)}},0) || 1;

  var key = catSort.key, dir = catSort.dir;
  rows.sort(function(a,b){{ return (a[1][key] > b[1][key] ? 1 : -1) * dir; }});

  document.getElementById('cat-body').innerHTML = rows.map(function(r){{
    var pct = (Math.abs(r[1].total)/grand*100).toFixed(1);
    return '<tr><td>'+r[0]+'</td>' +
      '<td class="amount '+(r[1].total>=0?'income':'expense')+'">'+(r[1].total>=0?'+':'')+r[1].total.toFixed(2)+'</td>' +
      '<td class="amount">'+pct+'%<span class="pct-bar" style="width:'+(pct*0.8)+'px;background:'+(r[1].total>=0?'var(--income)':'var(--expense)')+'"></span></td>' +
      '<td class="amount">'+r[1].count+'</td></tr>';
  }}).join('');
}}

// Transaction table
var txnSort = {{key:'date', dir:-1}};
var txnPage = 0;

function sortTxn(key) {{
  if (txnSort.key === key) txnSort.dir *= -1;
  else {{ txnSort.key = key; txnSort.dir = -1; }}
  txnPage = 0;
  renderAll();
}}

function renderTxn(filtered) {{
  var sorted = filtered.slice().sort(function(a,b){{
    var va = a[txnSort.key]||'', vb = b[txnSort.key]||'';
    return (va > vb ? 1 : -1) * txnSort.dir;
  }});
  var totalPages = Math.ceil(sorted.length / PAGE_SIZE) || 1;
  if (txnPage >= totalPages) txnPage = totalPages - 1;
  var page = sorted.slice(txnPage * PAGE_SIZE, (txnPage+1) * PAGE_SIZE);

  document.getElementById('txn-info').textContent = '共 '+sorted.length+' 笔';
  document.getElementById('txn-body').innerHTML = page.map(function(t){{
    return '<tr><td>'+t.date+'</td><td>'+esc(t.merchant)+'</td><td>'+t.category+'</td>' +
      '<td class="amount '+(t.type==='income'?'income':'expense')+'">'+(t.amount>=0?'+':'')+t.amount.toFixed(2)+'</td></tr>';
  }}).join('');

  var pagesHtml = '';
  for (var i=0; i<totalPages; i++) {{
    pagesHtml += '<button'+(i===txnPage?' class="active"':'')+' onclick="goPage('+i+')">'+(i+1)+'</button>';
  }}
  document.getElementById('txn-pages').innerHTML = pagesHtml;
}}

function goPage(n) {{ txnPage=n; renderAll(); }}

function resetFilters() {{
  document.getElementById('f-year').value = '';
  document.getElementById('f-month').value = '';
  document.getElementById('f-category').value = '';
  document.getElementById('f-search').value = '';
  document.getElementById('f-min').value = '';
  document.getElementById('f-max').value = '';
  updateMonthOptions();
  txnPage = 0;
  renderAll();
}}

function renderAll() {{
  var f = getFiltered();
  renderKPI(f);
  renderCat(f);
  renderTxn(f);
}}

// Theme
(function() {{
  var theme = localStorage.getItem('bankTheme') || 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  document.querySelector('.theme-btn').textContent = theme==='dark' ? '☀' : '☾';
}})();
function toggleTheme() {{
  var cur = document.documentElement.getAttribute('data-theme');
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('bankTheme', next);
  document.querySelector('.theme-btn').textContent = next==='dark' ? '☀' : '☾';
}}

function esc(s) {{ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
function debounce(fn, ms) {{ var t; return function(){{ clearTimeout(t); t=setTimeout(fn,ms); }}; }}

renderAll();
</script>
</body>
</html>'''


if __name__ == '__main__':
    main()
