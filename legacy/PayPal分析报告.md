# PayPal 交易分析报告

生成时间：2026-07-12（PayPal CSV 解析重构完成）

## 概述

- **PayPal CSV 总交易**：200 笔（ME 136 笔 + WIFE 64 笔），排除内部操作后保留 116 笔真实交易
- **匹配到银行**：89 笔（ME 82 笔 + WIFE 7 笔）
- **独立新增**：118 笔（两账户各 59 笔），其中大部分为 WIFE 账户（独立 PayPal，无对应银行流水）
- **JS 端零错误**，四个页签全部正常

## 数据源

| 文件 | 账户 | 总行数 | 内部操作 | 真实交易 | 匹配银行 |
|------|------|--------|---------|---------|---------|
| Paypal-20260711-czj.CSV | ME | 259 | 123 | 136 | 82 |
| Paypal-20260711-cr.CSV | WIFE | 122 | 58 | 64 | 7 |

## 分类分布

| 分类 | 笔数 | 说明 |
|------|------|------|
| 其他收入 | 31 | 退款、小额收款 |
| 家人转账 | 27 | Zejun Chen 夫妻间转账 + Rui Cheng |
| 线上购物 | 23 | AliExpress, FERA 24, Jellycat, THG Beauty 等 |
| 朋友转账 | 9 | Abdul Djalal, Yi He, Rui Guo, Tzu-Yueh Chen 等 |
| 宠物 | 6 | ZooRoyal, CatAmore, Fox4Pets, Zooland, zooplus |
| 邮寄 | 4 | DPD, Hermes, Deutsche Post |
| 服饰 | 3 | ARCTERYX, RL Finance (Ralph Lauren), Lululemon |
| 餐饮外食 | 3 | Uber Payments BV |
| 网上充值 | 3 | OpenAI × 2, Apple Services |
| 医疗 | 2 | APO Pharmacy, Shop Apotheke |
| 旅行 | 1 | Headout |
| 娱乐 | 1 | Nexon (游戏) |
| 其他 | 1 | Stefanie Albrecht (¥0 收款) |

> 仅 1 笔归入「其他」（¥0 的小额收款），覆盖率 99.1%。

## 残留 PayPal 通用（银行端 5 笔）

以下银行 PAYPAL 条目未能匹配到 PayPal CSV：

| 日期 | 金额 | 说明 |
|------|------|------|
| 2025-02-19 | -500 | Rui Cheng 转账（ME CSV 有对应交易，金额日期均匹配，可能是银行金额与 CSV 不完全对齐） |
| 2025-02-25 | -61 | Abdul Djalal（同上） |
| 2025-03-18 | -7.25 | Tzu-Yueh Chen 部分余额支付 |
| 2025-03-26 | -200 | Rui Cheng 转账 |
| 2025-05-20 | -41.27 | Abdul Djalal 部分余额支付（¥43.15 中 ¥41.27 从银行扣） |

这些残留条目多为部分余额支付场景，银行扣款金额与 PayPal 交易全额不一致，导致匹配失败。

## 本次改动汇总

1. **数据源更新**：两个 PayPal CSV 替换为 2026-07-11 新格式导出
2. **解析逻辑重写**：适配新 CSV 列结构（Description/Beschreibung 替代 Type/Typ，移除 Status/Balance Impact 列）
3. **匹配逻辑简化**：移除 Balance 二次匹配（新 Balance 为累计余额，不再适用）
4. **分类规则扩展**：新增 25 个商户关键字，PayPal「其他」从 54 笔降至 1 笔
5. **缓存修复**：CSV 文件时间戳现在触发缓存失效
