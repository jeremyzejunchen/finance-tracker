# PayPal 的工作模式

> 数据来源：`银行流水/Paypal-20260711-czj.CSV`（ME 账户，2025.01.03 ~ 2026.06.13）

## CSV 字段说明

第 4 列 `Description` — 交易类型
第 6 列 `Gross` — 交易金额（不含手续费，本 CSV 中 Fee 始终为 0）
第 7 列 `Fee` — 手续费
第 8 列 `Net` — 含手续费后的净额（= Gross + Fee，本 CSV 中 = Gross）
第 9 列 `Balance` — **交易后**的 PayPal 账户余额
第 12 列 `Name` — 商户名

## 概述

PayPal 账户绑定德国银行 Girokonto，采用 **"先付后扣"** 模式。用户购物时 PayPal 先垫付给商家，再从银行扣款补充到 PayPal。完整一次消费在 CSV 中体现为**两条紧邻的记录**（时间戳相同）。

---

## 核心流程一：消费

### 标准模式（全额从银行扣款）

**实例**（第 2-3 行，ZooRoyal ¥99.44）：

```
Express Checkout Payment   Gross=-99.44  Balance: 0 → -99.44   ← PayPal 付钱给商户
Bank Deposit to PP Account Gross=+99.44  Balance: -99.44 → 0   ← 从银行扣款补回
```

**规则**：
1. 商户发起扣款，总额为 Gross（负值）
2. 如果此前 Balance > 0，**优先用余额支付**，Balance 不够的部分才从银行扣；如果 Balance = 0，则 Balance 直接变负
3. 若 Balance 在消费后 < 0，下一行出现 `Bank Deposit to PP Account`，从银行扣款使 Balance 归零
4. 这笔 `Bank Deposit` 在银行账单上体现为 `PAYPAL EUROPE` SEPA 扣款，是**内部转账**

### 部分余额支付

**实例**（第 61-62 行，AliExpress ¥11.97）：

```
Express Checkout Payment   Gross=-11.97  Balance: 8.87 → -3.10  ← 先用余额 8.87
Bank Deposit to PP Account Gross=+3.10   Balance: -3.10 → 0     ← 只从银行扣 3.10
```

**结论**：`Bank Deposit Gross = |消费 Gross| - 消费前 Balance`

### 纯余额支付（无需银行扣款）

**实例**（第 249 行，OpenAI ¥23.00，此前 Balance=32.50）：

```
PreApproved Payment  Gross=-23.00  Balance: 32.50 → 9.50
```

消费后 Balance 仍 ≥0 → 无 Bank Deposit 行 → **银行账单上不出现这笔交易**。

---

## 核心流程二：退款

退款比消费复杂，核心是"钱退回 PayPal → 撤销银行扣款"。

### 标准退款：Payment Refund + ACH 逆转

**实例**（第 28-31 行，Tzu-Yueh Chen ¥18.25 退款）：

原消费（第 26-27 行）：

```
Mobile Payment             Gross=-18.25  Balance: 4.95 → -13.30  ← 余额付 4.95，不够扣
Bank Deposit to PP Account Gross=+13.30  Balance: -13.30 → 0     ← 银行扣 13.30
```

退款（第 28-31 行）：

```
Payment Refund                       Gross=+18.25  Balance: 0 → +18.25   ← 1. 全额退回 PayPal
User Initiated Withdrawal            Gross=-13.30  Balance: 18.25 → 4.95 ← 2. 银行扣款部分提现
Reversal of ACH Deposit              Gross=-13.30  Balance: 4.95 → -8.35 ← 3. 撤销 ACH 银行扣款指令
Reversal of ACH Withdrawal Transaction Gross=+13.30  Balance: -8.35 → 4.95 ← 4. 逆转第2步的提现
```

**简化理解**：退款 = 钱退回到 PayPal + ACH 层取消原始银行扣款。

### 信用卡退款（2026.05+）

**实例**（第 256-257 行，OpenAI ¥89.69 退款）：

```
Payment Refund           Gross=+89.69  Balance: 0 → +89.69
General Card Withdrawal  Gross=-89.69  Balance: +89.69 → 0
```

不再有 ACH Reversal，改为 Card Withdrawal 退回银行卡。

### AliExpress 部分退款（一单多笔包裹）

AliExpress 一个订单可能拆成多笔包裹发货，未发出的部分逐笔退款。每笔退款产生：
- `Payment Refund`（正数，退款金额）
- `User Initiated Withdrawal`（负数，提现退款部分）
- 可能有 `Reversal of ACH Deposit` / `Reversal of ACH Withdrawal Transaction`（在后续批量处理）

**实例**（第 71-87 行，¥11.97 订单共退 ¥13.52，分 6 笔）：

```
Refund +3.12 → Withdrawal -3.10  →  第1笔退款
Refund +0.97                     →  第2笔（纯余额，无 Withdrawal）
Refund +4.54 → Withdrawal -4.54  →  第3笔
Refund +1.75 → Withdrawal -1.75  →  第4笔
Refund +1.59 → Withdrawal -1.59  →  第5笔
Refund +4.54 → Withdrawal -4.54  →  第6笔
```

---

## 核心流程三：提现

### 手动提现到银行

**实例**（第 198-199 行，¥6000 收款后立即提现）：

```
Mobile Payment            Gross=+6000  Balance: 0 → +6000    ← 收到转账
User Initiated Withdrawal Gross=-6000  Balance: +6000 → 0    ← 提现到银行
```

银行账单上会出现 `PAYPAL EUROPE` SEPA 收入。

### 信用卡提现（2026.05+）

银行账单上不再出现 PAYPAL，改为银行/信用卡入账。

---

## 核心流程四：预授权冻结与解冻

用于验证账户有效性（如 eBay 绑定或酒店预授权）。

**实例**（第 77, 88-89 行）：

```
Account Hold for Open Authorization  Gross=-6.99  Balance: 8.87 → 1.88 ← 冻结
...（11 天后实际消费）...
Website Payment                      Gross=-6.99  Balance: 1.88 → -5.11 ← 正式扣款
Reversal of General Account Hold     Gross=+6.99  Balance: -5.11 → 1.88 ← 解冻
```

---

## 交易类型速查

### 消费类（Gross 为负 → 支出，需保留为交易记录）

| EN (ME 账户) | DE (WIFE 账户) | 场景 |
|-------------|----------------|------|
| `Express Checkout Payment` | `PayPal Express-Zahlung` | 网购（AliExpress, ZooRoyal, DB, Kaufland 等） |
| `Website Payment` | — | 网站支付（eBay 居多，商户名在 Name 列） |
| `Mobile Payment` | `Handyzahlung` | 手机转账（朋友间转账） |
| `PreApproved Payment Bill User Payment` | `Zahlung im Einzugsverfahren mit Zahlungsrechnung` | 预授权订阅（Vodafone, Shop Apotheke, Apple, OpenAI, Uber） |
| — | `Allgemeine Zahlung` | 通用支付（WIFE 独有，如 Juliette Owczarek） |

> **注**：`Payment Refund`（EN）/ 退款 虽然 Gross 为正，也是真实交易（收入），需保留。

### 内部转账类（需过滤，Gross 可正可负，不对应实际消费）

| EN (ME 账户) | DE (WIFE 账户) | 含义 |
|-------------|----------------|------|
| `Bank Deposit to PP Account` | `Bankgutschrift auf PayPal-Konto` | 从银行账户扣款补 PayPal |
| `General Card Deposit` | `Allgemeine Gutschrift auf Kreditkarte` | 从信用卡/借记卡扣款补 PayPal |
| `User Initiated Withdrawal` | `Von Nutzer eingeleitete Abbuchung` | PayPal 余额提现到银行/卡 |
| `General Card Withdrawal` | — | 提现到银行卡（ME 独有，2026.05+） |

### 冻结/ACH/逆转类（需过滤，银行层面不可见）

| EN (ME 账户) | DE (WIFE 账户) | 含义 |
|-------------|----------------|------|
| `Account Hold for Open Authorization` | `Einbehaltung für offene Autorisierung` | 预授权冻结（验证账户/押金） |
| `Reversal of General Account Hold` | `Rückbuchung allgemeiner Einbehaltung` | 释放预授权冻结 |
| `Reversal of ACH Deposit` | `Rückbuchung von ACH-Gutschrift` | 取消 ACH 银行扣款指令 |
| `Reversal of ACH Withdrawal Transaction` | — | 逆转 ACH 提现（ME 独有） |
| — | `ACH-Überweisung als Zahlungsquelle für Ausgleich von Kontoguthaben` | ACH 转账结算余额（WIFE 独有） |

---

## 资金来源演变

### ME 账户

| 时间段 | 资金来源 | CSV 记录 |
|--------|---------|---------|
| 2025.01 ~ 2026.04 | 银行 Girokonto | `Bank Deposit to PP Account` |
| 2026.05 ~ 至今 | 银行卡 | `General Card Deposit` / `General Card Withdrawal` |

### WIFE 账户

**混合使用**，从 2025.01 起就同时存在两种资金来源，无明确切换时间点：

| 资金来源 | CSV 记录 |
|---------|---------|
| 银行 Girokonto | `Bankgutschrift auf PayPal-Konto` |
| 信用卡/借记卡 | `Allgemeine Gutschrift auf Kreditkarte` |

可能是 PayPal 后台绑定了两种支付方式，系统自动选择。

---

## Balance 字段的含义

- Balance 是交易**后**的 PayPal 账户余额
- **不会自动归零**：只有 Bank Deposit/Card Deposit 补足后才恢复
- 消费前 Balance > 0 → Bank Deposit 金额 < 消费金额
- 消费前 Balance ≤ 0 → Bank Deposit 金额 = |消费金额 - 余额|

---

## 对银行账单解析的影响

### 解析时需要过滤的内部记录

以下 CSV 记录是 PayPal **内部流水**，不产生实际银行交易：

**ME (EN)**: `Bank Deposit to PP Account`, `General Card Deposit`, `General Card Withdrawal`, `User Initiated Withdrawal`, `Reversal of ACH Deposit`, `Reversal of ACH Withdrawal Transaction`, `Account Hold for Open Authorization`, `Reversal of General Account Hold`

**WIFE (DE)**: `Bankgutschrift auf PayPal-Konto`, `Allgemeine Gutschrift auf Kreditkarte`, `Von Nutzer eingeleitete Abbuchung`, `Rückbuchung von ACH-Gutschrift`, `Einbehaltung für offene Autorisierung`, `Rückbuchung allgemeiner Einbehaltung`, `ACH-Überweisung als Zahlungsquelle für Ausgleich von Kontoguthaben`

### 需要保留为交易记录的

**支出（Gross < 0）**: `Express Checkout Payment` / `PayPal Express-Zahlung`, `Website Payment`, `Mobile Payment` / `Handyzahlung`, `PreApproved Payment Bill User Payment` / `Zahlung im Einzugsverfahren mit Zahlungsrechnung`, `Allgemeine Zahlung`

**收入（Gross > 0）**: `Payment Refund`, `Mobile Payment` / `Handyzahlung`（正数）

### 匹配逻辑（待实现）

1. 从 PayPal CSV 提取真实消费/收入记录（排除上述内部类型）
2. 按 Gross **绝对值** + 日期（±5 天）匹配银行 `PAYPAL EUROPE` SEPA 条目
3. 支出匹配银行 PAYPAL → 用 PayPal 商户名覆盖银行描述
4. 收入匹配银行 PAYPAL → 标记银行为内部转账，保留 PayPal 收入
5. 未匹配 → 作为新交易加入

> **不再使用 Balance 做二次匹配**。旧逻辑把 Balance 误解为"银行扣款金额"，但新 CSV 的 Balance 是累计余额，无法用于匹配。且 WIFE CSV 已包含 Balance（Guthaben）列，两个 CSV 同构。
