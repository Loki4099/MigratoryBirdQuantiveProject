# M8：v0.22.0 Shadow、切换与回退

M8 以数据库 Release Control 为权威完成 v0.21/v0.22 双跑、N/N-1 Worker capability、代表性
Shadow Enrollment、SLO/告警、restore/rollback drill 和最终 cutover。所有 Gate 完成前不切换默认入口。

当前检查点：

- `M8_RELEASE_CONTROL_CHECKPOINT.md`：不可变发布状态机、Gate Evidence 和
  `maintenance_read_only` 恢复边界。
- `M8_SHADOW_PLAN_CHECKPOINT.md`：冻结每个 Context/Frequency 的代表性 Enrollment 与
  Execution Version，禁止跨身份合并观察期。
- `M8_SHADOW_COVERAGE_CHECKPOINT.md`：发布逐 Session v0.21/v0.22 Comparison，并按每个
  Representative 独立签发 fail-closed Coverage Snapshot。
- `M8_DUAL_RUN_SCHEDULER_CHECKPOINT.md`：冻结双腿 Runtime Binding，自动创建逐 Session Intent，
  并以 exact N/N-1 Worker Capability、TTL 与 fencing 执行。
- `M8_COMPARATOR_AUTOMATION_CHECKPOINT.md`：正式冻结 Comparator Policy 与 v0.21 Reference
  Decision，并自动汇合双腿发布 fail-closed Comparison。
- `M8_OPERATIONS_SLO_CHECKPOINT.md`：冻结六域 SLO Policy/Measurement，发布 Readiness，并为每个
  缺测、样本不足或阈值 breach 原子创建告警。
- `M8_RELEASE_RUNBOOK.md`：operator-only backup、restore/rollback evidence、只读 preflight、显式
  transition 与事故恢复操作顺序。

M8 工程能力已闭环；进入 `default` 仍必须由真实 Gate Evidence 通过 runbook 预检，不能以开发完成替代证据。
