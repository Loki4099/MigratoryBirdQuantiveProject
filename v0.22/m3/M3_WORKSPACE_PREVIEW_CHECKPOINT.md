# 候鸟 v0.22 M3 Workspace Preview 检查点

日期：2026-08-10
状态：`graph_preview_visible`，`persistent_draft_pending`

## 本检查点完成内容

- 新增由后端求解的 Graph Workspace Derived View；前端不自行推断祖先、投影、锁和聚合兼容性；
- 支持 Raw Input、加工层 1、加工层 2、加工层 3、Aggregation、Review 六个固定视图；
- 支持从上游正向选择：只改变下游 `ready/requires_ancestors` 状态，不自动选择下游；
- 支持从加工层 3 反向选择：自动展开完整人工血缘和逐层 projection；
- 支持 multi-output Node 的伴生输出，并保留不同 Feature Family；
- 支持共享祖先的完整 `locked_by` 消费者列表；
- selected/required Family 和 Variant 由后端排序并在前端置顶；
- 聚合器不兼容的 Stage 3 Feature 显示 `hard_incompatible`、原因文字和禁用操作；
- Bloodline Inspector 显示 producer kind、Node Variant、output port、origin stage、锁定来源和选择影响；
- Review 显示最终 Stage 3 输入、聚合器、分支数、Catalog 与 derived-state fingerprint；
- 新增 `/api/v2/workspace/graph-preview`，并同步 OpenAPI 与 TypeScript 生成类型；
- 新路由 `/workspace-v022` 与 v0.21 默认 Workspace 并存，版本标识随路由切换。

## 验收结果

- Workspace solver/API/graph/runtime 组合单测：15 passed；
- 前端 TypeScript、ESLint、生产构建：passed；
- 前端测试：25 passed；
- Ruff：passed；
- mypy：203 source files passed；
- 浏览器桌面与 390px 移动端验收：passed；
- 浏览器控制台 warning/error：0。

## 尚未完成

- Graph Draft 的持久化 revision、幂等事件和并发冲突处理；
- 锁定祖先的 cascade impact preview/confirm；
- Compile 命令与 Graph Suite 提交；
- 实际 Feature Payload 导出任务。本检查点的“导出本层血缘清单”只导出 Derived View manifest，
  不冒充数据输出；
- M3 Gate 全量回归与冻结。

Graph Draft 持久化、幂等事件与级联取消的后续进展见
[`M3_GRAPH_DRAFT_CHECKPOINT.md`](M3_GRAPH_DRAFT_CHECKPOINT.md)。

上述范围完成前，v0.22 Workspace 不替换 v0.21 默认入口。
