# M7 Common Panel 与 Result Evidence 发布检查点

日期：2026-08-11
状态：`passed`（M7 子检查点；M7 Gate 仍开放）

## Common Evaluation Panel

`CommonEvaluationPanelService` 发布精确、有序的 `(decision_session, asset_key)` mask。Panel：

- 不允许空集合、空 asset key、重复成员或非规范顺序；
- evidence class 只允许 `walk_forward_backtest`、`locked_historical_test`、`prospective_oos`；
- fingerprint 同时包含 evidence class、protected-context panel document 和完整有序成员；
- member ordinal 与 observation count 在数据库提交时再次校验；
- 相同 mask/context 幂等复用，不在比较查询时重新计算交集。

这使 controlled comparison 可以共享真正相同的样本 Artifact，而不是仅比较起止日期和行数。

## Result Evidence

`ResultEvidenceService` 将实际运行证据冻结为一个不可变 Artifact，同时绑定：

- 一个 published Result Artifact；
- 一个 published Research Configuration Snapshot；
- 零或一个同 evidence class 的 Common Evaluation Panel；
- exact runtime Artifact dependencies；
- resolved evidence document 与 quality document。

Evidence fingerprint 使用上游 Artifact semantic fingerprint，而不是数据库 UUID 猜测语义。同一 Result
最多绑定一份 Evidence；完全一致的重试复用既有发布，任何 interval、quality、Panel、Configuration 或
runtime dependency 漂移都会拒绝覆盖。

## 边界

- Configuration 描述“计划运行什么”；Evidence 描述“实际使用了什么”，两者不合并；
- Panel 缺失可用于某些非比较 Result，但不会在查询层临时生成 matched panel；
- runtime dependency 必须是 published Artifact，Lineage Manifest 保存精确依赖；
- 当前只完成发布身份，不宣称 Comparison classification 或 matched baseline 已完成。

## 验证

- Python unit：388 passed；
- PostgreSQL Graph Draft integration：5 passed；
- 验证两成员有序 Panel、Panel/Evidence 幂等重试和一 Result 一 Evidence；
- 验证改变 quality document 无法重写已发布 Result Evidence；
- Ruff 与 strict mypy 通过。

下一检查点实现 Comparison Scope、Protected Context、Treatment Dimensions 与 matched baseline 身份。
