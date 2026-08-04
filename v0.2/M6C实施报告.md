# M6C 实施报告：Strategy 只读 API 与双语界面

## 交付结果

M6C 将已经发布的 Strategy 研究对象接入只读产品界面：

- `/api/v2/strategies/overview` 分别返回 Rules、Products 和 Target Path 摘要；
- `/api/v2/strategies/targets/{artifact_id}` 按路径返回完整上下文和逐日决策；
- Rules 展示策略族、输入契约、9 个 K/过滤变体、周/月调仓计划和下一共同交易日开盘执行政策；
- Products 展示 Model Specification、Strategy Variant、Universe、Schedule 和 Execution Policy 组成的完整身份；
- Target Path 展示覆盖区间、决策数与候选记录数，并按调仓日检查全部候选资产；
- 每个候选资产展示 Model score、平均 rank、selection rank、趋势状态、策略资格、是否入选、目标权重与原因；
- 页面支持中文和英文，所有发布物均可继续进入通用 Artifact/Lineage 页面；
- OpenAPI 契约与生成的 TypeScript 类型同步更新。

## 边界

本页只回答“规则是什么、完整产品绑定了什么、某个决策日希望持有什么”。它不计算或展示成交、成本、净值、CAGR、Sharpe、回撤或排名。这些结果属于 M7 Experiment，避免把目标组合误称为可实现收益。

## 验证

- Python 单元测试：152 passed；
- PostgreSQL 集成测试：19 passed；
- 前端组件测试：8 passed；
- Ruff、Mypy、ESLint、TypeScript 与生产构建全部通过；
- 浏览器以真实 PostgreSQL 数据验证英文/中文切换、调仓日切换和发布物链接；
- 680px 视口下无页面级横向溢出，浏览器控制台无错误。
