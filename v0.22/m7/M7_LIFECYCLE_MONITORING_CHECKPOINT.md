# M7 Lifecycle 与 OOS Monitoring 检查点

日期：2026-08-11
状态：`passed`（M7 子检查点；M7 Gate 仍开放）

## Append-only Lifecycle

`EnrollmentLifecycleService` 不更新 Enrollment 根记录，而是发布有序、不可变 Lifecycle Event。
初始状态为 `active`，当前状态按给定 `as_of` 从已生效事件推导。支持的状态为：

- `active`；
- `suspended`；
- `superseded`；
- `retired`；
- `invalidated`。

每个事件冻结 sequence、from/to、明确 reason、requester、requested/effective time 和 Artifact
lineage。服务采用 expected sequence 做乐观并发检查；数据库再次验证事件必须延续 exact prior state、
effective time 不倒退且 transition 合法。终态不能重新激活。

## OOS Monitoring Snapshot

`OOSMonitoringService` 从同一 Enrollment 自动枚举截至 as-of session 的全部、且仅有
`oos_eligible=true` 的 Product Decisions。Snapshot 冻结：

- Monitoring Policy Version；
- Monitoring Engine Version Artifact；
- exact ordered Decision membership 与 fingerprints；
- as-of session / known-at；
- eligible、completed、missing counts；
- metrics、派生 health 与 reason codes。

qualification bridge、historical backfill、锚点前 Decision 和其他 Execution/Enrollment 的历史均不会
进入 Snapshot。数据库 member trigger 和 deferred completeness trigger 防止成员被删减、换序或跨产品
拼接。

Health 不能由调用者手填。当前冻结 Policy 使用 minimum completed decisions、maximum missing
fraction、coverage warning floor 和 coverage watch floor，系统确定性地产生
`observing / healthy / watch / warning / data_interrupted`。Monitoring Policy 可以独立升版：同一批
OOS Evidence 在 v1 下可为 healthy、在更严格 v2 下可为 watch，但不会改变 Execution Version 或
重启 OOS 时钟。Snapshot 还必须绑定 published Monitoring Engine Version Artifact；监控算法修复
发布新 Engine 身份并保留旧 Snapshot，不能覆盖历史健康结论。

## 验证

- Python unit：398 passed；
- PostgreSQL database foundation + Graph Draft integration：9 passed；
- 覆盖五类 health 派生结果；
- 验证 active → suspended → active 事件链、幂等重放与按时点状态推导；
- 验证 Snapshot 只包含一条 completed 和一条 missing prospective OOS Decision，排除 bridge gap；
- 验证 Monitoring v1/v2 独立升版导致可解释的 health 变化；
- 验证空库升级、全量降级及再次升级；
- Ruff 与 strict mypy 通过。

下一检查点交付 Experiment/Product 第一屏只读 API 与 M7 前端身份展示。
