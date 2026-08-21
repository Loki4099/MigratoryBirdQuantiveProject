# 候鸟 v0.22 M3 Gate

> Contract：`bird-migration-v0.22.0`
> Milestone：`M3 — 代表链 + 前端可见垂直切片`
> 状态：通过
> 完成时间：`2026-08-10T21:49:43+08:00`

## 1. 代表链与图语义

- 已发布并端到端求解 `return_continuation`、`price_cross_above_ma`、
  `low_illiquidity_quality` 三条人工代表链；覆盖连续加工、事件型加工、多输入节点、
  逐层 Projection 与 deterministic Aggregation。
- 每条边仍来自发布 Catalog 的固定 binding；Raw/较早 Stage 只能逐层投影，不跳层、
  不随机组合。
- 最终聚合输入只由 Stage 3 显式选择决定；上游 required occurrence 由 Solver 派生并锁定。

## 2. 持久化 Workspace

- Graph Draft 根、不可变 Revision、追加式 Event、级联 Change Preview 和精确 Revision
  Compile 已形成完整写模型。
- 客户端所有选择通过带 expected revision 的事件保存；冲突刷新、命令幂等、no-op 审计、
  级联确认与 lost-response 恢复均由后端契约控制。
- Asset Context 与 Resolved Data Binding 完全由后端解析已发布身份；Draft 冻结完整解析文档
  与 fingerprint，浏览器不提供哈希占位符。
- Compile 重新求解当前精确 Revision，并桥接至不可变旧编译输入；Compiled Graph 继续按
  graph fingerprint 复用。

## 3. 前端可见切片

- v0.22 独立 Workspace 支持双向选择、非法项禁用、selected-first、三层自动血缘、
  Family/Variant 展示、每层导出、血缘检查、聚合层与 Review Compile。
- 刷新页面可恢复同一 Draft；页面显示后端 revision、Catalog version、Asset Context、
  主动/required/最终输入计数与 Compile fingerprint。
- 浏览器真实验收覆盖最终信号选择、逐层自动点亮、刷新恢复、血缘抽屉和成功 Compile，
  控制台无 error。

## 4. 数据库与兼容性

M3 新增四个顺序 migration：

1. `20260810_55_v022_projection`：固定 binding 的逐层 Projection；
2. `20260810_56_v022_graph_draft`：Draft/Revision/Event/Change Preview；
3. `20260810_57_v022_compile_bridge`：精确 Draft Revision 编译绑定；
4. `20260810_58_v022_graph_context`：后端解析的 Asset Context/Data Binding 文档。

唯一 Alembic head 为 `20260810_58_v022_graph_context`。空库升级、M1/M2 增量升级、
降级至 base 后再次升级均由回归测试覆盖；正式数据库写入数为 0。

## 5. 验证结果

- 全量 Python pytest：391 passed，1 个上游 Starlette TestClient deprecation warning；
- API/OpenAPI：26 passed；v0.22 unit：27 passed；Graph Draft PostgreSQL integration：2 passed；
- Ruff、mypy：通过；Alembic 唯一 head：通过；
- 前端 TypeScript、ESLint、25 个 Vitest、production build：通过；
- `git diff --check`：通过；
- 两个无关 v0.1 用户文档未修改、未暂存。

因此 `m4_entry_allowed=true`。下一阶段迁移 28 Factor/51 Signal 并生成独立 parity evidence。
v0.22 Workspace 继续保持独立入口；v0.21 默认入口、86 Model/Strategy/Defense、全量大 Catalog
前端与 Product cutover 均未提前切换。前端可见检查点之后只允许优化布局、颜色、文字和操作反馈，
不得改变已冻结的图、身份与数据语义。
