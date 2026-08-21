# 候鸟 v0.22 M4 全量兼容 Runtime 检查点

日期：2026-08-11

## 已完成

- 新增 `LegacyCompatibilityRuntime`，从有效 Migration Registry 读取旧 recipe，不在代码中
  复制第二份 Factor/Signal 配置。
- 28 个 Factor Variant 全部通过统一 v0.22 identity 执行；输入、参数、实现键及 warm-up
  观测数均来自冻结 recipe。
- 51 个具体 Signal Version 全部执行；连续排序、threshold state、crossover event 各自使用
  已发布的 normalization、tie、missing、direction 与 rule 契约。
- Signal 执行前强制校验其 Factor 血缘，不能把同 payload 类型但不属于该人工线路的 Factor
  接入 Signal。
- runtime contract、输入和完整执行结果分别生成确定性 fingerprint；相同输入重复执行完全一致。

## Registry 修订

runtime 接入发现 `migration-registry.v0.22.1.json` 的 recipe 不足以独立执行：

1. 离散 Signal 错误继承 `average_rank`，实际契约应为 `normalization=none`、
   `tie_policy=not_applicable`；
2. Factor recipe 未记录 `required_price_observations`。

当时有效 Registry 为 `migration-registry.v0.22.2.json`，fingerprint：
`3da4379a2c249fec3c46db4e10d70715d09445415012b87e91fdb418b7b88afe`；后续逐点比较发现
Signal candidate-only 输入边界，已由 `0.22.3` 取代。
修订不改变任何 family/variant identity、stage、Oracle binding 或 Catalog 节点。

## 验证

- 全量兼容 runtime：28 Factor、51 Signal，两次确定性执行通过。
- 单元测试：349 passed。
- Ruff：通过；mypy strict：207 个 source file 通过。
- PostgreSQL 空库发布 475 个不可变 Catalog component，并编译全部 51 个 Stage 3 Signal：通过。

## 尚未完成

本检查点证明完整兼容计算入口已可执行，但不等同于独立 parity Evidence。当前 runtime 有意适配
冻结的 v0.21 计算实现；下一步必须以 M0 已保存的 Artifact/hash 为外部期望值逐项比较，发布
28/28 Factor 与 51/51 Signal 的独立 Evidence，之后才能把 Registry 状态提升为
`parity_passed`。
