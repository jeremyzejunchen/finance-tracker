# 交易分类方案

> 编辑此文件后告诉 Claude，会自动更新 `parse_bank_pdfs.py` 中的分类规则。

## 分类结构

- **第一层**：`固定支出` / `活动支出` / `收入`
- **第二层**：具体子类
- **第三层**（仅交通）：油费 / 停车费 / 汽车税 / 罚款 / 公共交通

---

## 固定支出

定期、可预测、必要性开支。

| 子类 | 关键字/商户 | 说明 |
|------|-----------|------|
| 房租 | Harald Windel | 含 Nebenkosten |
| 电费 | E.ON Energie, Stadtwerke Göttingen | 电力和市政 |
| 广电费 | Rundfunk ARD | 德国强制电视广播费 |
| 网费 | Telekom Deutschland | 手机和宽带 |
| 话费 | Vodafone | 手机话费 |
| 健康保险 | Techniker Krankenkasse | 公立医保 |
| 其他保险 | GOTHAER ALLGEMEINE, HANSEMERKUR, SIGNAL IDUNA | 第三方责任险等 |
| 车险 | Volkswagen Autoversicherung, HUK-COBURG, Sparkassen DirektVersicherung | 汽车保险 |
| 汽车保养 | VW Leasing | 车贷月供 |
| 健身 | Finion Capital, FITNESS FUTURE, A.I. Fitness | 健身房月费订阅 |

---

## 活动支出

可变、可选性开支。

| 子类 | 关键字/商户 | 说明 |
|------|-----------|------|
| 超市日用品 | KAUFLAND, LIDL, ALDI, REWE, DM-DROGERIE, GO ASIA, EDEKA, NETTO, ROSSMANN, Penny, Action, Handelshof | 日常食物和日用品 |
| 线上购物 | AMAZON, ALIEXPRESS, EBAY, TAOBAO, ZALANDO, UNIQLO, AMZN Mktp, SP ORCHIDEEN-KLUSMAN, SP CHINA MARKT CHEMN, Nespresso, PAYPAL *ALIPAY, Deloox, infigo, OCHAMA, DocMorris, Joybuy | 网购各类商品 |
| 线下购物 | OBI, IKEA, PRIMARK, DOUGLAS, TEGUT, MIX MARKT | 实体店消费 |
| 餐饮外食 | KFC, MCDONALD, BURGER KING, UBER EATS, LIEFERANDO, TANIA MOHAMED, UMG GASTRONOMIE, CAFE, LUTZ MICHAEL, ROXX, DOOBOO THE GLEN, ChiliChillen Hotpot, Eiscafe, SPC.Mamame, COCO TEA FRESH, PAMPAS GENT, THE OLIVE STREETFOOD, PAVILLON STADT | 外食和外卖 |
| 汽车/交通 | 见下方三级分类 | 所有交通相关支出 |
| 宠物 | FRESSNAPF, ZOOPLUS, ZOOROYAL, TIERARZT, TIERAERZTLICHES, DR. MED. VET. WYSTUB | 兽医和粮食 |
| 医疗 | APOTHEKE, KRANKENHAUS, SANITATSHAUS, DRK, ARBEITER-SAMARITER, Shop Apotheke | 药房和医疗自费 |
| 旅行 | BOOKING, AIRBNB, LUFTHANSA, HOLIDAY INN, CHECK24, GOODMORNINGBERLIN, PREUSS.SCHLOSSER, Hamb. Elbphilharmonie | 酒店和出行 |
| 服饰 | LULULEMON, ZALANDO, PRIMARK, UNIQLO, 优衣库, 宜家 | 衣物 |
| 娱乐 | NETFLIX, SPOTIFY, CINEMAXX | 流媒体和影院 |
| 学费 | Georg-August-Universitat, UMG, Gottingen Stiftung, Heenemann | 大学相关扣款 + 医学教材 |
| 市政缴费 | Stadt Goettingen, STADT GOETTINGEN, Buergerbuero, Generalkon. der VR China | 市政和官方缴费 |
| 网上充值 | Google CLOUD, APPLE.COM/BILL, OpenAI | 云服务和订阅（含 ChatGPT） |
| 邮寄 | Deutsche Post | 邮局 |
| 家人转账 | Rui Cheng | 给家人的汇款 |
| 朋友转账 | Abdul Rahman Djalal, YANG SUN, PAYPAL *catherine2013, Yuxiao Luo, Siwen Yuan | 给朋友的汇款 |
| 投资 | SpaceX | IPO/投资相关 |
| 押金退回 | Catella Real Estate AG | 押金类 |
| PayPal通用 | PAYPAL | PayPal 未识别具体商户时 |
| 其他 | | 未能匹配到以上规则的交易 |

### 汽车/交通（三级分类）

| 三级子类 | 关键字/商户 | 说明 |
|---------|-----------|------|
| 油费 | ARAL, SHELL, TOTAL, bft, PFEFFER, STAR GOTTINGEN, OIL 413, CLASSIC TANKSTELLE, Esso, LEO Herzberg | 加油 |
| 停车费 | Parkster, ParkDepot, CONTRIPARK, PARKAUTOMATEN, PARKEN, Parking, Contipark, Bezirksamt Charlottenburg | 停车 |
| 汽车税 | Bundeskasse, Kfz-Steuer | 机动车税 |
| 罚款 | Stadt Kassel Verkehrsueberw | 交通罚单 |
| 公共交通 | DEUTSCHE BAHN, DB VERTRIEB, DE LIJN | 火车/公交/地铁 |

---

## 收入

| 子类 | 关键字/商户 | 说明 |
|------|-----------|------|
| 工资 | Dres. Dekowski, Renner | 诊所工资 |
| 大学薪资 | Georg-August-Universitat, UMG, Gottingen Stiftung | 大学和研究机构 |
| 奖学金/津贴 | Catella Real Estate, Sparkassen DirektVersicherung | 房租补贴和保险返还 |
| 二手收入 | Mangopay, Diana Buck, Karin Mollberg | 二手物品出售 |
| 投资收入 | Tesla, Core S&P 500, Netflix, Saveback, Stockperk | 股票奖励和返现 |
| 利息收入 | Interest payment, Credit interest, Leipzig Account | 账户利息 |
| 其他收入 | PAYPAL, 其他人 | 退款和其他收入 |

---

## 不参与统计的类别

这些交易在解析阶段自动标记，不影响分类规则：

| 标记 | 触发条件 |
|------|---------|
| 内部转账 | 交易涉及自有 IBAN（DB/TR/WIFE 之间转账） |
| 换汇 | 商户名以 `PAYM.ORDER` 开头 |
| 失败交易 | ±3 天内同一金额同一人一进一出 |
