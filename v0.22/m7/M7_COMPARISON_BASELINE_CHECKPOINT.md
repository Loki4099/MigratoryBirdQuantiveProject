# M7 Comparison 与 Matched Baseline 检查点

日期：2026-08-11
状态：`passed`（M7 子检查点；M7 Gate 仍开放）

## Comparison 身份

`ResultComparisonService` 只比较两份已经发布的 Result Evidence，并明确声明
`predictive`、`portfolio` 或 `replication_audit` scope。每份 Evidence 必须为该 scope
提供 protected context；Common Evaluation Panel fingerprint 与 evidence class 也属于受保护上下文。

比较器从两份 Research Configuration Snapshot 自动提取固定的 Treatment Dimensions：

- `aggregation_algorithm`；
- `aggregation_direct_inputs`；
- `aggregation_training_target`；
- `aggregation_hyperparameters`；
- `strategy_selection`；
- `defense_package`。

调用者不能手写 classification。系统按 fingerprint 和逐维差异确定：相同配置为
`replication`，相同 protected context 且仅一维变化为 `controlled`，多维变化为
`multi_axis`，protected context 不同或无法解释的配置差异为 `incompatible`。
数据库再次约束 comparable comparison 必须共享 protected context，且 controlled
必须恰好有一个变化维度。

## Matched Baseline 身份

`MatchedBaselineService` 发布带版本的 append-only assessment，而不是在查询或页面展示时
寻找“最相近”的结果。当前支持：

- `defense_none`：必须是仅 `defense_package` 变化的 controlled comparison，主体侧有
  Defense，基线侧严格为 `none`；
- `deterministic_aggregation`：必须是仅 `aggregation_algorithm` 变化的 controlled
  comparison，主体侧为 trainable，基线侧严格为 deterministic；
- 若正式匹配不存在，发布 `missing` assessment，并保存非空 reason codes；不得临时替代。

服务层与数据库触发器都校验 comparison 必须包含 exact subject/baseline pair、变化方向正确，
并禁止对同一 `(subject, baseline_kind, assessment_version)` 覆盖写入不同证据。

## 验证

- Python unit：393 passed；
- PostgreSQL Graph Draft integration：5 passed；
- 验证 Defense → none 的 controlled comparison、幂等重放和显式 missing assessment；
- 验证反向 none → Defense 不能被登记为 `defense_none` baseline；
- Ruff 与 strict mypy 通过。

下一检查点实现稳定 Product Definition，以及相互独立的 Product Execution Version、
Qualification Version、Monitoring Policy Version 三类冻结身份。
