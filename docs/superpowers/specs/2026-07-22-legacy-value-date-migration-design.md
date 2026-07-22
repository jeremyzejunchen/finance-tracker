# 旧账本 value_date 兼容迁移设计

## 目标

让缺少 `transactions.value_date` 的历史 SQLite 账本在启动时安全升级，使 CSV 导入可以写入账本。

## 决策

采用最小列迁移：仅在列缺失时添加 `value_date TEXT NOT NULL DEFAULT ''`。迁移前沿用现有哈希校验备份机制；已有交易不改写，后续导入写入解析出的值日期。

不实现通用的全表结构修复器，因为已确认的失败点只有该列，扩大迁移范围会增加对真实账本的风险。

## 验证

- 合成旧数据库缺少 `value_date` 时，`Database.initialize()` 创建备份并补齐该列。
- 补齐后通过 `FinanceService.confirm_many()` 成功写入合成 CSV 交易。
- 项目完整测试通过，并使用现有固定 Playwright CLI 运行一次临时数据库的导入浏览器回归。
