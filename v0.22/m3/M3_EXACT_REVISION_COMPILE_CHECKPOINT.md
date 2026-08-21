# 候鸟 v0.22 M3 精确 Revision 编译检查点

日期：2026-08-10

## 本检查点已完成

- 新增追加式 `workspace.v022_graph_draft_compile_binding`，一一绑定
  `Graph Draft + revision` 与不可变旧编译输入身份。
- 桥接 `draft_intent_id` 由 Graph Draft ID 与 revision 确定性生成；旧编译输入的
  `revision` 与 Graph Draft revision 保持一致，不重新从 1 编号。
- Compile 在持有 Graph Draft 根行锁期间重新求解 Derived View，不信任客户端缓存。
- Compile 只接受当前精确 `expected_revision`；成功不会增加 Draft revision。
- 当前确定性聚合器只有一个参数 preset 时，桥接器显式冻结该 preset；多个 preset
  且用户尚未选择时返回 `aggregation_parameter_preset_required`，不静默任选。
- 编译继续复用既有 Artifact、血缘、Graph fingerprint、Compile Attempt 与关系投影。
- 同一个 idempotency key 返回同一个 Compile Attempt；新命令可产生新的 Attempt，
  但语义相同的 Compiled Graph 按 fingerprint 复用。
- Draft 根的 `last_compiled_research_graph_id` 仅作为最近结果指针；完整审计仍由
  binding、Compile Attempt 与 Artifact 血缘承担。
- 新增冻结 API：
  `POST /api/v2/workspace/graph-drafts/{graph_draft_id}/compile`。
- OpenAPI 与前端 TypeScript 类型已同步。

## 验证结果

- PostgreSQL 空库迁移到 `20260810_57_v022_compile_bridge` 成功。
- Graph Draft/Compile PostgreSQL 集成测试：2 passed。
- 相关 Python 单元测试：38 passed。
- Ruff、mypy：通过。
- 前端 TypeScript、ESLint：通过。
- 前端 Vitest：25 passed。
- 前端 production build：通过；既有单 chunk 大于 500 kB 警告不阻塞本检查点。

## 后续完成

前端 Graph Provider/Compile UI、后端 Asset Context 与 Resolved Data Binding 解析、全量回归
和迁移审计已经完成，结论见
[`M3_GRAPH_CONTEXT_PROVIDER_CHECKPOINT.md`](M3_GRAPH_CONTEXT_PROVIDER_CHECKPOINT.md) 与
[`M3_GATE.md`](M3_GATE.md)。v0.22 Workspace 仍保持独立入口，不替换 v0.21 默认入口。
