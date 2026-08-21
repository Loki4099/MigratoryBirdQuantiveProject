# 候鸟 v0.22 M3 Graph Draft 持久化检查点

日期：2026-08-10

## 本检查点已完成

- 新增独立的 `Graph Draft` 根身份；草稿根只保存当前 revision 与最近编译引用，
  不复用旧的不可变编译输入身份。
- 新增不可变 `Graph Draft Revision`，每个 revision 同时冻结 Intent、选择 fingerprint、
  Derived View fingerprint 与完整 Derived View。
- 新增追加式 Draft Event；每个写命令带 actor、idempotency key、请求 fingerprint、
  base/resulting revision 与是否实际产生变更。
- 实现事务级 `FOR UPDATE` 并发控制、旧 revision 冲突、幂等重放与同 key 异载荷拒绝。
- no-op 事件写审计记录但不增加 revision。
- 普通取消不能移除已被下游显式选择锁定的祖先；必须先生成带过期时间的级联影响预览，
  再一次性确认。确认会形成新的不可变 revision，并消费 preview token。
- 新增并冻结以下 API：
  - `POST /api/v2/workspace/graph-drafts`
  - `GET /api/v2/workspace/graph-drafts/{graph_draft_id}`
  - `POST /api/v2/workspace/graph-drafts/{graph_draft_id}/events`
  - `POST /api/v2/workspace/graph-drafts/{graph_draft_id}/change-previews`
  - `POST /api/v2/workspace/graph-drafts/{graph_draft_id}/change-previews/{impact_token}/confirm`
- OpenAPI 与前端 TypeScript 客户端类型已同步生成。

## 验证结果

- PostgreSQL 迁移从空库升级到 `20260810_56_v022_graph_draft` 成功。
- Graph Draft/API 定向测试：30 passed。
- Ruff：通过。
- mypy（`src`）：通过。
- 前端 TypeScript、ESLint：通过。
- 前端 Vitest：25 passed。
- 前端 production build：通过；现有单 chunk 大于 500 kB 的 Vite 警告不阻塞本检查点。

## 后续完成

前端 Graph Provider、后端 Context 解析、全量回归与迁移审计已经完成，结论见
[`M3_GRAPH_CONTEXT_PROVIDER_CHECKPOINT.md`](M3_GRAPH_CONTEXT_PROVIDER_CHECKPOINT.md) 与
[`M3_GATE.md`](M3_GATE.md)。v0.22 Workspace 仍保持独立入口，不替换 v0.21 默认入口。

精确 revision 编译的后续进展见
[`M3_EXACT_REVISION_COMPILE_CHECKPOINT.md`](M3_EXACT_REVISION_COMPILE_CHECKPOINT.md)。
