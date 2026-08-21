# 候鸟 v0.22 M4 Migration Registry 检查点

日期：2026-08-11

> 更新：Registry `0.22.0` 在后续 Catalog 相邻层与 Node Family 固定 stage 校验中
> 发现 8 条 stage 错误，先由 `0.22.1` 取代；兼容 runtime 接入时又发现 recipe 未冻结
> 离散信号策略及 Factor warm-up 观测数，先形成 `0.22.2`；独立逐点比较又发现 Signal
> 必须冻结 candidate-only 输入作用域，当前有效版本为 `0.22.3`。旧版本保留审计；
> 错误与修正规则记录在 `M4_CATALOG_CHECKPOINT.md`。

## 已完成

- 从冻结 v0.2 Factor/Signal Catalog 建立 28 个 Factor Variant 与 51 个具体 Signal Version
  的逐项 Migration Registry，不以 Family 通过替代 Variant/Version 验收。
- 每条记录冻结 legacy family、recipe、精确 v0.22 Family/Variant、origin stage 与 Stage 3
  可选择语义。
- 每个对象绑定 M0 不可变 Oracle 中的两个独立数据上下文：共 56 个 Factor 输出、102 个
  Signal 输出，合计 158 个 Artifact/hash 绑定。
- 状态机严格区分 `mapped`、`catalog_validated`、`executable`、`oracle_bound`、
  `parity_passed`、人工审查和发布批准；blocked 必须说明原因，waiver 不能计为 parity。
- `parity_passed` 及其后状态必须绑定独立 Evidence Artifact ID。
- 生成器支持从冻结输入确定性重建与 `--verify` byte-semantic 校验；源 Catalog 或 Oracle
  fingerprint 漂移会立即失败。

## 当前盘点

- Factor Variant：28/28 已映射；其中 M3 已部署 3 条为 `executable`。
- Signal Version：51/51 已映射；其中 M3 已部署 3 条为 `executable`。
- Oracle binding：158/158。
- 状态分布：`mapped=73`、`executable=6`、`parity_passed=0`。
- 有效 Registry fingerprint 为
  `3bf4781b0762fbaad704308c073cd5f6742915a6212514e1d6b9c6e622e48cac`。

## 验证

- Registry 专项与既有 Catalog contract：9 passed。
- Ruff：通过；mypy：通过。
- 负向测试覆盖缺少具体 Version、Oracle content hash 漂移和数量不符。

本检查点只冻结完整迁移台账，不声称 M4 parity 完成。下一步是将 73 条 `mapped` 记录按
人工 Catalog 部署为兼容 Processing Node/Projection，并把 Registry/Evidence 发布为不可变
数据库 Artifact。
