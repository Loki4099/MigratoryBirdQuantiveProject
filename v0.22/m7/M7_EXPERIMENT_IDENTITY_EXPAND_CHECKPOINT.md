# M7 Experiment Identity Expand 检查点

日期：2026-08-11
状态：`passed`（DB-8 expand 子检查点；M7 Gate 仍开放）

## 交付范围

新增 Alembic revision `20260811_62_v022_exp_identity`，建立：

- `experiment.v022_research_configuration_snapshot`；
- `experiment.v022_configuration_direct_input`；
- `experiment.v022_common_evaluation_panel` 及有序成员；
- `experiment.v022_result_evidence_snapshot`。

Configuration 将 `semantic_identity_document`、`provenance_document` 和 `display_document` 分列保存。
只有 semantic document 参与 configuration fingerprint；来源 Catalog/Draft/branch 和显示名称不会因
展示变化错误重启 OOS。

## 数据库约束

- Configuration 的 Branch 必须属于同一 Compiled Graph；
- direct inputs 必须逐 ordinal 等于该 Branch 的 Aggregation input，且延迟到事务提交检查完整数量；
- Common Panel 是有序 `(decision_session, asset_key)` mask，成员数必须等于冻结 observation count；
- Result Evidence 只能引用 published Result Artifact；
- Evidence 与 Common Panel 的 evidence class 必须一致；
- evidence class 只允许 `walk_forward_backtest`、`locked_historical_test`、`prospective_oos`；
- 五张表全部 append-only，发布后不能 UPDATE/DELETE。

本检查点仅完成 expand 和约束，不宣称 Snapshot 发布、Comparison 或 Product runtime 已完成。

## 验证

- 空库升级到唯一 head 通过；
- 完整降级到 base 后重新升级通过；
- 既有 lineage、Artifact lifecycle、Engine/Run 数据库约束回归通过；
- unit database-head 检查与 Ruff 通过。
