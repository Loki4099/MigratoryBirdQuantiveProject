# M6 Draft Checkpoint、Revision Clone 与 Catalog Rebase 检查点

日期：2026-08-11
状态：`passed`（M6 子检查点；M6 Gate 仍开放）

## 交付边界

- Graph Draft 的每个不可变 revision 都是可追溯 checkpoint，并分别固定 `catalog_release_id`。
- 用户可从任意已存在 revision 创建独立 Draft；克隆记录来源 Draft/revision，复制冻结的 Intent、Derived View、资产上下文与数据绑定，不跟随来源后续变化。
- 旧 Catalog Draft 不允许普通事件或编译，返回 `409 catalog_rebase_required`。
- Catalog rebase 必须先生成影响预览，再使用有期限的 `impact_token` 确认；不会猜测或自动替换被删除的 Feature、Aggregation、Strategy 或 Defense。
- 确认前若 Draft revision 或目标 Catalog 发生变化，操作 fail-closed，必须重新预览。

## API

- `POST /api/v2/workspace/graph-drafts/{draft_id}/clones`
- `POST /api/v2/workspace/graph-drafts/{draft_id}/rebase-previews`
- 复用 `POST /api/v2/workspace/graph-drafts/{draft_id}/change-previews/{impact_token}/confirm` 完成确认。

前端工作台提供“Clone current revision”和“Check Catalog update”入口。Rebase 预览显示来源与目标 Catalog Release，以及将被移除的显式选择；用户二次确认后才切换。

## 数据库

Alembic head：`20260811_61_v022_checkpoint`。

- `workspace.v022_graph_draft_revision.catalog_release_id` 固定历史 revision 的 Catalog 身份。
- `workspace.v022_graph_draft` 新增成对的 clone provenance 字段与复合外键。
- Rebase 在单个事务内写入新 revision、更新 Draft 当前 revision/Catalog，并消费预览 token。

## 验证

- Python unit：380 passed。
- Graph Draft PostgreSQL integration：3 passed，覆盖 clone 幂等、来源隔离、旧 Catalog 命令拒绝、rebase 预览/确认及历史 revision Catalog 固定。
- 数据库迁移与 v0.22 Catalog/runtime integration：13 passed。
- Frontend：TypeScript typecheck 通过；25 tests passed。
- Ruff 与 mypy 通过；OpenAPI JSON 和生成的 TypeScript schema 已同步。

本检查点不宣称 M6 Gate 完成；资源预估/准入、批量命令队列、多标签页并发和完整 Review 仍属于后续 M6 工作。
