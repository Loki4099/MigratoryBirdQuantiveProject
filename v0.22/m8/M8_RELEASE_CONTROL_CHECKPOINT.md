# M8 Release Control 检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-11T17:30:00+08:00`

## 1. 数据库权威状态

新增 append-only `workspace.v022_release_transition`，发布路径为：

```text
hidden → shadow → explicit_eligible → default
                    ↘ maintenance_read_only
```

允许从 `default` 降级到 `explicit_eligible` 或 `shadow`；事故回退进入
`maintenance_read_only`。不提供回到 `hidden` 的路径，因为回退不能删除或伪装已发布的 v0.22 对象。
当前状态只从最新 published Transition Artifact 推导；数据库没有记录时 fail-closed 为 `hidden`。

## 2. Evidence 边界

- `shadow` 要求已发布 Shadow Plan；
- `explicit_eligible` 要求已发布 parity gate；
- `default` 要求 parity、代表性 shadow coverage、operations readiness、DB/Object Store restore
  drill 和 rollback drill；
- 进入 maintenance 必须保存非空 Incident Document；
- 离开 maintenance 额外要求 incident impact analysis、parity、restore 和 rollback drill。

所有证据进入 Transition Artifact lineage。应用层验证 Artifact 已发布，数据库触发器再次验证精确前态、
序号和合法转换；Transition 发布后禁止 UPDATE/DELETE。

`shadow_plan_artifact_id` 和 `shadow_coverage_artifact_id` 还会验证精确 Artifact Type；Coverage 必须存在
对应 Snapshot 且 `ready_for_default=true`。仅伪造同名普通 Artifact 或失败 Snapshot 无法进入 default。

## 3. 路由语义

状态对象显式返回 default contract、shadow runtime、v0.21 research creation、v0.22 explicit creation
和 maintenance read-only 五类标志，调用方不能用单一布尔值猜测路由。此检查点尚未切换 API 默认入口；
mutation guard 将在后续切片接入。

## 4. 验证

- Python unit：399 passed；
- 全 v0.22 PostgreSQL integration：21 passed；
- Release Control 定向 PostgreSQL 演练：1 passed；
- v0.21 历史增量升级、空库升级、base downgrade/upgrade 和只读 API revision：5 passed；
- Ruff、strict mypy 和 `git diff --check` 通过。

本检查点只建立可审计的发布权威，不生成伪造的 shadow coverage，也不授权进入 `default`。
