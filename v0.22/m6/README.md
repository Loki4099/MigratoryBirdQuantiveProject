# M6：Workspace 全量前端

M6 将 M3 的代表性 Graph Workspace 扩展为可承载完整 Catalog、资源准入与完整研究分支 Review
的正式工作台。本目录记录各个可独立验收的纵向检查点；在所有检查点完成前不宣称 M6 Gate 通过。

当前检查点：

- `M6_CATALOG_PAGING_CHECKPOINT.md`：服务端 Family 搜索、cursor pagination、统一 view token、
  Pinned 独立区，以及前端一致分页接入。
- `M6_CHECKPOINT_REBASE_CHECKPOINT.md`：不可变 revision checkpoint、固定来源 clone、
  Catalog rebase 影响预览与 fail-closed 确认。
- `M6_RESOURCE_ADMISSION_CHECKPOINT.md`：冻结结构资源预估、准入 blocker、分支轴指纹，
  以及 Review 资源摘要。
- `M6_BATCH_MUTATION_QUEUE_CHECKPOINT.md`：原子 Feature batch、FIFO revision 队列、pending
  行状态和 409 暂停/恢复。
- `M6_MULTI_TAB_CONCURRENCY_CHECKPOINT.md`：BroadcastChannel revision notice、跨标签自动重读，
  以及活动队列的 fail-closed 暂停。
- `M6_CONFIGURATION_REVIEW_CHECKPOINT.md`：Aggregation preset、Strategy、Defense 的显式 Draft
  配置、兼容性 blocker、精确分支编译与完整 Review。
- `M6_PERFORMANCE_ACCESSIBILITY_CHECKPOINT.md`：有界 Catalog 首屏、路由拆包、对话框焦点约束
  与键盘验收。
- `M6_GATE.md`：M6 的统一 Gate 结论、fail-closed 边界、回归证据与 M7 入口。

M6 Gate 已通过。后续从 M7 Experiment/Product 身份与连续运行继续。
