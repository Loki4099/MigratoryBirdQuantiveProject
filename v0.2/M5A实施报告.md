# M5A 实施报告：Model 目录与不可变规格

## 交付结果

本阶段建立 Model 层的计算逻辑身份，但不提前计算市场数据结果。

- 新增严格 Model Catalog 契约和确定性规格展开器；
- 物化 `weighted_mean`、`majority_vote`、`weighted_vote` 三个方法及其版本；
- 建立 `classic_market_model` 定义与两级“维度—信号”结构版本；
- 生成 51 个 single-signal、31 个维度子集等权、2 个固定权重和 2 个 directional-vote 规格；
- 共保存 86 个 specification、151 个 specification-local dimension 和 331 个 component；
- vote 在维度输出上应用 `sign` 后再投票，保留 neutral tie；不伪装为普通连续均值；
- 每个 component 直接外键引用准确的 published Signal version；
- Model Catalog 必须追溯到其 M0 指定的 Signal Catalog 及对应 materialization，不解析任意 latest；
- 新增 `style-rotation model bootstrap` CLI；
- 新增 Alembic 迁移、ORM、单元测试和真实 PostgreSQL 集成测试。

## 关键约束

Dimension 只存在于具体 specification 内，不回写成 Signal 的永久分类。同一 specification 中一个 Signal 只能出现一次。所有持久化维度和组件权重必须大于零且不超过一，各层的和在发布前由严格契约验证为一。缺失输入策略冻结为 `require_complete_inputs`，禁止运行时对剩余输入静默重新归一化。

## 验证结果

- ruff format/check：通过；
- mypy：通过；
- Model/CLI 针对性单元测试：18 passed；
- Model PostgreSQL 发布集成测试：通过；
- M0–M5A 完整真实 PostgreSQL 回归：144 passed；
- 空库迁移、降级/升级、重复发布去重、精确血缘和 published child freeze：通过。

现有两个 warning 分别来自第三方 Starlette/httpx 迁移提示和受限环境无法写 `.pytest_cache`，不影响研究逻辑或数据库结果。

## 下一步

M5B 实现纯 Model 计算器、版本化 Model Engine、component-to-Signal-Dataset 显式输入映射，以及 Model Dataset/Value 原子发布。
