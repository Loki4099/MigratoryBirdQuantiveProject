# M8 双跑调度与 Worker Capability 检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-11T23:30:00+08:00`

## 1. 冻结双腿运行身份

`workspace.v022_shadow_runtime_binding` 为每个 Shadow Representative 冻结：

- v0.21 execution specification 与可选的正式 Product Enrollment；
- v0.22 exact Product Enrollment/Execution Version；
- Comparator Version；
- 两条执行腿各自的 compiler、executor、environment fingerprint 与 capability key。

`active_product_shadow` 必须绑定当时仍为 active 的 v0.21 Enrollment。该腿只产出用于比较的
reference Artifact，不创建第二条正式 v0.21 Product Decision；正式 Enrollment 仍由 pinned v0.21
runtime 连续运行。`shadow_only` 不得冒用正式 v0.21 Enrollment，也不驱动资金。

## 2. 自动、幂等的逐 Session 调度

`ShadowDualRunScheduler` 只在 Release Control 为 `shadow`、`explicit_eligible` 或 `default` 时工作。
它从 Representative 的 first eligible session 开始，扫描 cutoff 已到达的 Schedule Session，并为每期
原子创建一个不可变 Dual-run Intent 和恰好两个 Work Item。重复扫描不会重复建单；调度顺序按
session cutoff 优先，保证同一会话的 N-1/N 配对先于下一会话。

若 active v0.21 Enrollment 已不再 active，后续调度 fail closed。即使 rollback 与扫描发生竞态，
worker claim 仍会再次检查 Release Control，`maintenance_read_only` 下不会继续领取 Shadow 工作。

## 3. N/N-1 Worker Capability

Worker 的 service principal 由进程认证上下文注入，不从任务载荷自报。Capability Lease 带 TTL，claim
必须同时精确匹配 runtime contract、compiler、executor、environment fingerprint 和 capability key。
同一 worker 可以登记 N 与 N-1 两组能力，但任何一个字段不同都不能误领任务。

Work lease 使用 fencing token；过期 lease 可以重新领取，旧 worker 不能提交结果。v0.21 完成必须给出
published reference Artifact；v0.22 完成必须给出属于 exact Representative Enrollment 与 Session 的
Product Decision。

## 4. 验证

- Python unit：407 passed；v0.22 PostgreSQL integration：22 passed；
- capability policy：exact match、N/N-1 共存及非法 identity fail closed；
- PostgreSQL revision 70：冻结 Binding、两期四任务、重复调度零新增；
- 过期 capability 不能 claim，exact worker 能按 session 顺序完成两腿；
- Ruff、strict mypy 与迁移链检查通过。

本检查点完成自动调度与安全执行领取。完成双腿后的 Comparator 自动发布、SLO/告警、restore/rollback
drill、API mutation guard 与最终 cutover Gate 仍属于后续 M8 切片。
