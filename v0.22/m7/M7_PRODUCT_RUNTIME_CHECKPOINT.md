# M7 Enrollment 与 Product Decision 运行身份检查点

日期：2026-08-11
状态：`passed`（M7 子检查点；M7 Gate 仍开放）

## 冻结 Decision Schedule

`DecisionScheduleService` 发布不可变 Schedule Version，并冻结每个 session 的：

- canonical ordinal；
- session date；
- timezone-aware decision cutoff。

Schedule 不允许空集合、重复日期、乱序日期或非递增 cutoff。数据库的 deferred
completeness trigger 再次验证成员数量、连续 ordinal 和严格时间顺序。

## Enrollment 与 OOS 锚点

`ProductEnrollmentService` 将一个 Product Execution Version 精确绑定到：

- Qualification Version；
- Monitoring Policy Version；
- Decision Schedule Version；
- `oos_anchor_cutoff_at`；
- `activation_effective_at`；
- `first_eligible_decision_session_id`。

第一场合法 session 由系统选择，必须是 cutoff 严格晚于
`max(oos_anchor_cutoff_at, activation_effective_at)` 的最早成员。调用者不能跳过更早的
合法 session。Schedule frequency 必须等于 Execution Configuration frequency；同一
Execution Version 只能拥有一个冻结 Enrollment，配置变化必须建立新 Execution 与新 OOS 时钟。

## 不可变 Product Decision

`ProductDecisionService` 以 `(Product Execution Version, Decision Session)` 为唯一身份。
completed Decision 冻结：

- exact input Manifest；
- nullable active Model State；
- Aggregation Run；
- Strategy Target；
- Defense Decision；
- merged final target；
- evidence class、decision/quality documents 与 Artifact lineage。

v0.22.0 deterministic Execution 强制 `active_model_state_artifact_id = NULL`。五个运行
Artifact 必须全部已发布且互不混用。计划 session 未运行时必须发布 `missing` Decision 和非空
reason codes，所有运行输出为空，不允许静默沿用上一场结果。

只有 `prospective_oos` 且 session ordinal 不早于 Enrollment 的 first eligible session，
`oos_eligible` 才为 true；qualification bridge、historical backfill 与锚点前 session 永不计入 OOS。

## 验证

- Python unit：393 passed；
- PostgreSQL database foundation + Graph Draft integration：9 passed；
- 验证系统选择第一场合法 OOS session、Enrollment 幂等重放；
- 验证 bridge gap 不计 OOS、prospective completed/missing Decision 正确计入；
- 验证 deterministic Decision 拒绝非空 active Model State；
- 验证空库升级、全量降级及再次升级；
- Ruff 与 strict mypy 通过。

下一检查点实现 Enrollment lifecycle events、OOS Monitoring Snapshot 与连续运行健康状态。
