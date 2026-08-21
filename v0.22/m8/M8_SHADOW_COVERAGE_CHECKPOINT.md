# M8 Shadow Comparison 与 Coverage 检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-11T19:30:00+08:00`

## 1. 逐 Session Comparison

新增不可变 `workspace.v022_shadow_decision_comparison`。每条记录精确绑定 Shadow Representative、
Decision Session、v0.22 Product Decision、v0.21 reference Artifact 和 Comparator Version。

Outcome 只有 `matched`、`different`、`missing_v021`。v0.22 missing Decision 不能标记 matched；
Comparison 必须在 Decision cutoff 后 known，且 Decision 必须属于该 Representative 的 exact Enrollment、
Execution Version 并为 prospective OOS eligible。解释性差异必须带非空 reason codes，未解释差异保持显式。

## 2. Coverage Snapshot

`workspace.v022_shadow_coverage_snapshot` 与有序 member stats 按 Plan 和 Comparator 独立发布：

- 每个 Representative 分别统计 eligible sessions、Comparison、matched、explained/unexplained、
  missing v0.21 和 missing v0.22；
- weekly 必须由同一 Enrollment/Execution 独立达到 12 期，monthly 独立达到 3 期；
- 两个 weekly Representative 各 6 期不能合并成 12 期；
- 所有 eligible sessions 截至 Snapshot known-at 都必须有 Comparison；
- missing v0.21、missing v0.22 和 unexplained difference 全部阻断；
- Plan 必须覆盖 ETF、large-cap、weekly、monthly，并为每个 Context 补齐 frequency matrix。

部分 Plan 或尚在积累中的 Plan 可以发布 `ready_for_default=false` Snapshot，以保留真实进度；不能签发
可用于 cutover 的假阳性证据。

## 3. Release Control 接线

`shadow_coverage_artifact_id` 现在必须属于 `v022_shadow_coverage_evidence`，并且其数据库 Snapshot
`ready_for_default=true`。因此 default transition 不能只依赖调用方提交的 UUID 或 Artifact 名称。

## 4. 验证

- Coverage evaluator：3 passed，覆盖禁止跨 Representative 合并、完整矩阵通过、missing/unexplained
  fail-closed；
- 完整 Product fixture：逐 Session matched + missing/unexplained Comparison、失败 Coverage 与幂等重放通过；
- 当前完整门禁基线：Python unit 402，v0.22 PostgreSQL integration 21；
- Ruff、strict mypy、revision 69 迁移和 `git diff --check` 通过。

本检查点发布比较与证据身份；自动双跑调度仍属于后续 M8 切片。
