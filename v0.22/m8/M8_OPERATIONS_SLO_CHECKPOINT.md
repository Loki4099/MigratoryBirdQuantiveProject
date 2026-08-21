# M8 Operations SLO 与告警检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-12T01:30:00+08:00`

## 1. 六个强制 SLO 域

`ops.v022_slo_policy_version` 冻结用于 cutover 的规则。每条规则声明 metric、方向、阈值、最小样本量
与告警级别；一个可用于 default Gate 的 Policy 必须覆盖：

- compile；
- queue；
- cache；
- storage；
- export；
- product freshness。

系统不从零散时间戳推测指标。`ops.v022_slo_measurement` 保存由明确 probe 产生的观测值、样本量、完整
窗口、measured-at、probe identity 和不可变 fingerprint。

## 2. Readiness 与 Alert

`ops.v022_operations_readiness_snapshot` 只比较同一精确窗口内的 Measurement。每条 Policy Rule 都会
生成一个 Member：

- 缺失 Measurement：`missing_measurement:<metric>`；
- 样本量不足：`insufficient_samples:<metric>`；
- 超出阈值：`slo_breach:<metric>`；
- 仅在观测存在、样本足够且阈值通过时标记 passed。

每个失败 Member 原子创建一条 `ops.v022_operational_alert`，绑定 Snapshot、Rule、Measurement（若
存在）、severity 和 blocker code。数据库 deferred completeness trigger 保证 Member 数等于 Rule 数，
Alert 数等于失败 Rule 数，不能签发缺页的假阳性 Readiness。

## 3. Release Gate

`operations_readiness_artifact_id` 现在必须：

1. 是 published `v022_operations_readiness_evidence`；
2. 存在正式 Readiness Snapshot；
3. `ready_for_default=true`。

任意普通 Artifact、失败 Snapshot 或缺测 Snapshot 都不能充当 default Gate 证据。

## 4. 验证

- Python unit：415 passed；v0.22 PostgreSQL integration：23 passed；
- 六域全部通过：ready、零 Alert、幂等重放；
- queue breach + Product freshness 缺测：两条 blocker、两条 Alert；
- 失败 Snapshot 被 Release Control 拒绝；
- revision 72、Ruff、strict mypy 与 append-only/完整性约束通过。

本检查点建立 SLO 证据契约和告警事实层。具体 probe 采集器可以按运行模块逐步替换测试 probe，但不得
绕过 Measurement 身份。后续 M8 继续 DB + Object Store restore drill、rollback drill、API mutation
guard 与最终 cutover Gate。
