# M6A 实施报告：Strategy 契约与不可变版本

## 交付结果

M6A 将策略层从占位目录升级为可发布、可追溯的正式领域对象，但尚不计算目标仓位、净值或绩效。

- `StrategyCatalog` 对目录执行严格机器校验；
- 三种过滤语义与 K=1/2/3 展开为 9 个独立 Strategy Variant；
- K=2 标记为 canonical，K=1/3 标记为 sensitivity；
- 周频、月频调仓计划各自版本化；
- 下一共同交易日 adjusted open 执行政策独立版本化；
- 趋势变体精确引用已发布的 `price_above_ma_state__moving_average_ratio__s1_l200` Signal；
- Strategy Definition、Version、Input Contract 和 Variant 均通过统一 Artifact 发布、血缘依赖与不可变触发器保护；
- CLI 新增 `style-rotation strategy bootstrap`；
- Strategy Product Definition/Version 表已建立，M6A 保持为空，供 M6B 绑定模型、资产池、调仓计划和执行政策。

## 关键边界

Strategy Variant 是通用执行规则，不是完整对外产品。只有加入 Model Specification、Universe、Schedule 与 Execution Policy 后，Strategy Product Version 才成为可实验和可比较的完整身份。

策略只消费模型分数和已发布的辅助状态，不按模型名称分支，也不在策略代码中重算因子或信号。缺失模型分数、候选资格或趋势状态会阻止正式运行，不能静默转换成 reserve。

本阶段没有生成策略收益。目标仓位属于 M6B，组合会计、双边成本、净值、多区间绩效与排行榜属于 M7。

## 验证

- Ruff：通过；
- Mypy：通过；
- Strategy/CLI 单元测试：通过；
- PostgreSQL 空库迁移、降级/升级：通过；
- Strategy Catalog 首次发布与重复发布去重：通过；
- 精确 Signal 外键与发布后不可修改：通过。
