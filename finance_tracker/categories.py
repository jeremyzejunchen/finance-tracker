from __future__ import annotations


# Display order is part of the seed data, not parser code. Operators can change it in the UI.
DEFAULT_CATEGORIES = [
    ("收入", "薪酬", "工资", "income"),
    ("收入", "补贴与退款", "补贴", "income"),
    ("收入", "投资与利息", "利息", "income"),
    ("收入", "个人转入", "朋友与家人", "income"),
    ("收入", "其他收入", "待分类", "income"),
    ("固定支出", "住房", "房租与物业", "expense"),
    ("固定支出", "账单与通信", "水电网费", "expense"),
    ("固定支出", "保险", "保险费", "expense"),
    ("固定支出", "订阅", "订阅服务", "expense"),
    ("固定支出", "固定车辆成本", "车贷与车险", "expense"),
    ("可变支出", "食品日用", "超市", "expense"),
    ("可变支出", "餐饮", "外食与外卖", "expense"),
    ("可变支出", "购物与服饰", "购物", "expense"),
    ("可变支出", "交通", "公共交通", "expense"),
    ("可变支出", "交通", "油费与停车", "expense"),
    ("可变支出", "医疗健康", "医疗", "expense"),
    ("可变支出", "宠物", "宠物支出", "expense"),
    ("可变支出", "娱乐", "娱乐", "expense"),
    ("可变支出", "旅行", "旅行", "expense"),
    ("可变支出", "教育", "教育", "expense"),
    ("可变支出", "政府与手续费", "政府缴费", "expense"),
    ("可变支出", "社交与第三方转出", "转给他人", "expense"),
    ("转账与调整", "自有账户转账", "内部转账", "excluded"),
    ("转账与调整", "冲正", "冲正交易", "excluded"),
    ("可变支出", "其他", "其他", "expense"),
    ("收入", "其他收入", "其他收入", "income"),
    ("投资", "现金流", "入金", "investment"),
    ("投资", "证券交易", "买入", "investment"),
    ("投资", "证券交易", "卖出", "investment"),
    ("投资", "收益与费用", "分红", "investment"),
    ("投资", "收益与费用", "费用", "investment"),
]

LEGACY_LEAVES = {
    "expense": ["房租", "电费", "广电费", "网费", "话费", "健康保险", "第三方责任险", "车险", "汽车保养", "健身", "超市日用品", "线上购物", "线下购物", "餐饮外食", "油费", "停车费", "汽车税", "罚款", "公共交通", "宠物食品", "宠物保险", "兽医", "医疗", "旅行", "服饰", "娱乐", "学费", "市政缴费", "网上充值", "邮寄", "家人转账", "朋友转账", "PayPal通用", "其他"],
    "income": ["工资", "大学薪资", "奖学金/津贴", "二手收入", "投资收入", "利息收入", "退款", "其他收入"],
}

LEGACY_RULES = {
    "超市日用品": r"KAUFLAND|LIDL|ALDI|REWE|DM.DROGERIE|GO ASIA|EDEKA|NETTO|ROSSMANN|PENNY|ACTION|HANDELSHOF",
    "餐饮外食": r"KFC|MCDONALD|BURGER KING|LIEFERANDO|GASTRONOMIE|CAFE|EISCAFE|STREETFOOD",
    "线上购物": r"AMAZON|AMZN|ALIEXPRESS|EBAY|TAOBAO|OCHAMA|VINTED|JELLYCAT|SMYTHS|JOYBUY",
    "油费": r"ARAL|SHELL|TOTAL|BFT|TANKSTELLE|ESSO|OIL 413",
    "停车费": r"PARKSTER|PARKDEPOT|CONT.RIPARK|PARKAUTOMATEN|PARKING",
    "公共交通": r"DEUTSCHE BAHN|DB VERTRIEB|DE LIJN",
    "医疗": r"APOTHEKE|KRANKENHAUS|SANIT.TSHAUS|ARBEITER.SAMARITER",
    "宠物食品": r"FRESSNAPF|ZOOPLUS|ZOOROYAL|GRANATAPET|ZOOLAND",
    "兽医": r"TIERARZT|TIERAERZT|MED. VET", "房租": r"HARALD WINDEL",
    "电费": r"E.ON ENERGIE|STADTWERKE G.TTINGEN", "网费": r"TELEKOM DEUTSCHLAND",
    "话费": r"VODAFONE", "健身": r"FINION CAPITAL|FITNESS FUTURE|A.I. FITNESS",
    "利息收入": r"INTEREST PAYMENT|CREDIT INTEREST|LEIPZIG ACCOUNT",
}

for bucket, leaves in LEGACY_LEAVES.items():
    level1 = "收入" if bucket == "income" else "活动支出"
    for leaf in leaves:
        item = (level1, leaf, leaf, bucket)
        if item not in DEFAULT_CATEGORIES:
            DEFAULT_CATEGORIES.append(item)
