# M7：Experiment/Product 身份与连续运行

M7 实现 DB-8，并把 M6 编译结果推进为不可漂移的 Experiment、Comparison 与 Product 连续决策。
所有切片完成前不宣称 M7 Gate 通过，也不切换 v0.22 默认入口。

当前检查点：

- `M7_EXPERIMENT_IDENTITY_EXPAND_CHECKPOINT.md`：Research Configuration、Result Evidence 与
  Common Evaluation Panel 的 append-only 数据库身份和 fail-closed 约束。
- `M7_CONFIGURATION_SNAPSHOT_PUBLICATION_CHECKPOINT.md`：从精确 Compiled Branch 发布三类文档、
  有序 direct inputs、Artifact lineage 与语义幂等性。
- `M7_PANEL_EVIDENCE_PUBLICATION_CHECKPOINT.md`：发布精确有序 Common Evaluation Panel，并将
  Result、Configuration、Panel、runtime dependencies 与质量结果冻结为 Result Evidence。
- `M7_COMPARISON_BASELINE_CHECKPOINT.md`：按 scope、protected context 与固定 Treatment
  Dimensions 自动分类比较，并发布有方向、可缺失、带版本的 matched baseline assessment。
- `M7_PRODUCT_VERSION_IDENTITY_CHECKPOINT.md`：建立稳定 Product Definition，并将 Execution、
  Qualification、Monitoring Policy 三类版本分离为不可变、可独立演进的身份。
- `M7_PRODUCT_RUNTIME_CHECKPOINT.md`：冻结 Decision Schedule 与 Enrollment OOS 锚点，并发布
  exact runtime lineage、可显式缺失且不可跨 Execution 拼接的 Product Decision。
- `M7_LIFECYCLE_MONITORING_CHECKPOINT.md`：以 append-only events 推导 Enrollment 生命周期，
  并从 exact prospective Decision membership 发布版本化 OOS health Snapshot。
- `M7_IDENTITY_API_FRONTEND_CHECKPOINT.md`：提供 Experiment/Product 冻结身份只读 API，并在两个
  旧页面第一屏展示最终 Aggregator、有序 direct signals、Strategy、Defense 与连续运行状态。
- `M7_GATE.md`：冻结 M7 的实现边界、fail-closed 约束和完整验证证据。

M7 Gate 已通过，允许进入 M8；默认入口仍保持不变。
