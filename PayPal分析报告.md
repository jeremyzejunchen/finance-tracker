# PayPal 交易分析报告

生成时间：2026-07-11

## 概述

- **PayPal CSV 解析交易**：51 笔（均未匹配到银行流水，作为新交易添加）
- **ME 账户**：25 笔 | **WIFE 账户**：26 笔
- **剩余 PayPal通用（银行端未匹配）**：20 笔

---

## 一、PayPal CSV 交易分类分布

| 分类 | 笔数 | 金额 (EUR) | 备注 |
|------|------|-----------|------|
| 其他 | 21 | -4,577.58 | 主要为 WIFE 账户，需进一步细分 |
| 其他收入 | 10 | +71.19 | 退款、小额收款 |
| 线上购物 | 9 | -245.61 | AliExpress、Joybuy、Nespresso |
| 家人转账 | 6 | 0.00 | Rui Cheng 之间 0 EUR 转账 |
| 网上充值 | 2 | -112.69 | OpenAI / ChatGPT |
| 宠物 | 1 | -40.34 | zooplus |
| 邮寄 | 1 | -1.80 | Deutsche Post |
| 朋友转账 | 1 | -51.70 | Siwen Yuan |

### 分类详情

#### ME 账户 (25 笔)

**线上购物 (9 笔, EUR -245.61)**：
| 日期 | 金额 | 商户 |
|------|------|------|
| 2025-07-19 | -31.17 | AliExpress |
| 2025-07-23 | -9.17 | AliExpress |
| 2025-07-24 | -23.39 | AliExpress |
| 2025-10-23 | -9.30 | AliExpress |
| 2026-03-23 | -0.01 | Joybuy（已标记为失败交易） |
| 2026-03-26 | -12.37 | AliExpress |
| 2026-03-30 | -19.34 | Joybuy |
| 2026-06-13 | -10.86 | Joybuy |

**网上充值 (2 笔, EUR -112.69)**：
| 日期 | 金额 | 商户 |
|------|------|------|
| 2026-05-23 | -23.00 | OpenAI Ireland Limited |
| 2026-06-05 | -89.69 | OpenAI Ireland Limited（ChatGPT Pro） |

**其他收入 (10 笔, EUR +71.19)**：
- AliExpress 退款 × 2（+3.51, +0.30）
- Joybuy 退款 × 1（+0.01，已标记失败）
- OpenAI 退款 × 1（+9.69）
- Abdul Djalal × 2（+8.99, +2.50）
- Rui Cheng × 2（+40.00, +1.20）

**家人转账 (6 笔, EUR 0.00)**：
- 6 笔 Rui Cheng 之间 0 EUR 转账（标记为内部转账）

**朋友转账 (1 笔, EUR -51.70)**：
- 2026-05-31 Siwen Yuan

#### WIFE 账户 (26 笔)

**线上购物 (1 笔, EUR -130.00)**：
| 日期 | 金额 | 商户 |
|------|------|------|
| 2025-11-07 | -130.00 | Nespresso Deutschland GmbH |

**宠物 (1 笔, EUR -40.34)**：
| 日期 | 金额 | 商户 |
|------|------|------|
| 2025-10-25 | -40.34 | zooplus SE |

**邮寄 (1 笔, EUR -1.80)**：
| 日期 | 金额 | 商户 |
|------|------|------|
| 2026-05-23 | -1.80 | Deutsche Post AG |

**其他收入 (2 笔, EUR +5.99)**：
| 日期 | 金额 | 商户 |
|------|------|------|
| 2025-08-19 | +0.99 | Yaobin WU |
| 2026-06-12 | +4.00 | Chuyao Wan |

**其他 (21 笔, EUR -4,577.58)** — 需要进一步分类：

| 日期 | 金额 | 商户 | 可能分类建议 |
|------|------|------|-------------|
| 2025-07-25 | -100.72 | FERA 24 UG (eBay) | 线上购物 |
| 2025-08-10 | -4.94 | MGP Vinted | 线上购物 |
| 2025-08-20 | -106.00 | Jellycat Limited | 线上购物 |
| 2025-08-26 | -41.55 | THG Beauty Europe GmbH | 线上购物/美妆 |
| 2025-08-26 | 0.00 | Zejun Chen | 家人转账 |
| 2025-10-09 | -35.00 | Birong Xu | 其他 |
| 2025-10-14 | 0.00 | Zejun Chen | 家人转账 |
| 2025-10-14 | -35.96 | FERA 24 UG (eBay) | 线上购物 |
| 2025-10-16 | -3.00 | Juliette Owczarek | 其他 |
| 2025-11-03 | -6.90 | DPD Deutschland GmbH | 邮寄 |
| 2025-11-25 | 0.00 | Rui Cheng | 家人转账（已算在 ME） |
| 2025-11-26 | -2,000.00 | Zejun Chen | 家人转账 |
| 2025-11-27 | -123.75 | RL Finance BV | 其他 |
| 2026-01-30 | -2,000.00 | Zejun Chen | 家人转账 |
| 2026-02-21 | 0.00 | Zejun Chen | 家人转账 |
| 2026-03-02 | 0.00 | Zejun Chen | 家人转账 |
| 2026-03-21 | -49.43 | FERA 24 UG (eBay) | 线上购物 |
| 2026-04-17 | -39.95 | Zooland (宠物用品) | 宠物 |
| 2026-05-22 | 0.00 | Stefanie Albrecht | 其他 |
| 2026-06-12 | -21.20 | Zejun Chen | 家人转账 |
| 2026-06-23 | -3.99 | Hermes Germany GmbH | 邮寄 |
| 2026-06-29 | -5.19 | Hermes Germany GmbH | 邮寄 |

---

## 二、未匹配到银行的 PayPal 交易

**所有 51 笔 PayPal CSV 交易均未匹配到银行流水**。原因分析：

1. **日期范围**：PayPal CSV 覆盖 2025-07 至 2026-06，而银行 PDF 流水中的 PayPal 条目多数已被分类关键字匹配掉（如 AliExpress、CinemaxX 等出现在 details 字段中的可识别商户）
2. **WIFE 账户**：银行流水中没有 WIFE 账户的 PayPal 交易（WIFE 使用独立 PayPal 账户），因此无法匹配
3. **小额交易**：部分小额交易（< EUR 5）可能在银行流水中无对应记录

### 匹配逻辑回顾

当前 `match_paypal_to_bank()` 的匹配条件：
- 金额绝对值一致（容差 ±0.01）
- 日期 ±5 天内
- 银行流水商户名包含 "PAYPAL"

匹配失败的常见原因：
- 银行流水中的 PayPal 交易金额与 PayPal CSV 不完全一致（如手续费差异）
- WIFE 账户的 PayPal 交易不在 ME 的银行流水中
- 银行流水中对应条目已被之前的分类规则匹配消耗

---

## 三、剩余 PayPal通用（银行端，20 笔）

这些是银行流水中的 "PayPal Europe" 条目，已分类为「PayPal通用」，未匹配到 PayPal CSV：

| 日期 | 金额 | Details 中的线索 |
|------|------|-----------------|
| 2025-01-06 | -99.44 | ZooRoyal Petcare GmbH（宠物） |
| 2025-01-14 | -5.00 | 无商户信息 |
| 2025-01-21 | -16.54 | 无商户信息 |
| 2025-02-19 | -500.00 | 无商户信息 |
| 2025-02-25 | -61.00 | 无商户信息 |
| 2025-03-18 | -7.25 | 无商户信息 |
| 2025-03-21 | -30.78 | CinemaxX Entertainment（娱乐） |
| 2025-03-26 | -200.00 | 无商户信息 |
| 2025-05-06 | -14.50 | 无商户信息 |
| 2025-05-07 | -10.19 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-05-07 | -11.99 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-05-15 | -12.99 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-05-20 | -41.27 | 无商户信息 |
| 2025-05-27 | -12.99 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-06-04 | -16.98 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-06-09 | -80.00 | 无商户信息 |
| 2025-06-16 | -12.99 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-06-17 | -8.58 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-06-17 | -10.99 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |
| 2025-06-17 | -13.99 | mc-eur-plux-issuing.paypal.com（PayPal 卡消费） |

**注意**：标记为 "mc-eur-plux-issuing.paypal.com" 的交易是通过 PayPal 虚拟借记卡 (PayPal Business Debit Mastercard) 发起的消费，这些交易的详细信息在银行端不包含真实商户名，只能通过 PayPal CSV 匹配识别。

### 可手动改进的分类

| Details 线索 | 建议分类 | 金额 |
|-------------|---------|------|
| ZooRoyal Petcare GmbH | 宠物 | -99.44 |
| CinemaxX Entertainment | 娱乐 | -30.78 |

---

## 四、建议改进

1. **WIFE 的「其他」分类**：21 笔中有大量可识别商户（FERA 24 = eBay 线上购物、Jellycat = 线上购物、DPD/Hermes = 邮寄、zooplus/Zooland = 宠物、Zejun Chen = 家人转账），建议添加分类关键字
2. **PayPal 卡消费**：9 笔 mc-eur-plux-issuing 类交易无法从银行端识别，需要 PayPal CSV 覆盖该时间段（2025-05 至 2025-06）的 PayPal 卡交易明细
3. **匹配率提升**：当前 PayPal CSV 交易 100% 未匹配银行流水，主要原因是 WIFE 账户无银行流水对应。ME 账户的 25 笔理论上可与银行流水匹配，但当前匹配算法可能因金额/日期差异导致失配
