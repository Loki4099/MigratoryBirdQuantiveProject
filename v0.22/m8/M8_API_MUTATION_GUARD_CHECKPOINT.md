# M8 API Mutation Guard Checkpoint

状态：完成

## 准入矩阵

API mutation 不再根据前端显示状态自行判断，而是在每次真实 operation 开始前读取
数据库权威 `workspace.v022_release_transition`：

| Release state | v0.21 新研究 | v0.22 Graph 研究 | Product 运维 | Suite cancel | 历史导出 |
|---|---:|---:|---:|---:|---:|
| `hidden` | 允许 | 禁止 | 允许 | 允许 | 允许 |
| `shadow` | 允许 | 禁止 | 允许 | 允许 | 允许 |
| `explicit_eligible` | 允许 | 允许 | 允许 | 允许 | 允许 |
| `default` | 禁止 | 允许 | 允许 | 允许 | 允许 |
| `maintenance_read_only` | 禁止 | 禁止 | 禁止 | 禁止 | 允许 |

这里的历史导出不改变研究或 Product 身份，因此按冻结计划在维护只读态继续开放。
operator 批准的 pinned runtime/replay 不通过公共研究 mutation API，仍走独立运维路径。

## 已守卫入口

- v0.21：Draft save、Suite submit、Experiment promotion；
- v0.22：Graph Draft create/clone/event、两类持久化 change preview、preview confirm、compile；
- 运维：Suite cancel、Product lifecycle、Alert status、Product review；
- 导出：Signal research export 明确声明 `historical_export` scope。

`workspace/compile-preview` 与顶层 `workspace/graph-preview` 是无持久化纯预览，维护态仍可用。
Graph change preview 会写入 TTL Preview 记录，虽然名字含 preview，仍按 mutation 处理。

## 幂等与错误契约

v0.21 Command 的准入检查位于 `CommandIdempotencyService` 的 operation 内：

- 已存在的同 key/同 payload 响应可以在切换后精确回读；
- 新 key 或不同 payload 必须重新经过当前 Release State 准入；
- 被拒绝请求不会创建 Draft、Suite、Product 或新的幂等结果。

拒绝响应为稳定 `mutation_admission_blocked`：普通切换冲突返回 HTTP 409，
`maintenance_read_only` 返回 HTTP 423，并携带 `scope`、`release_state`、`reason_code`。

## 前端状态接口

新增 `GET /api/v2/release-control`，返回：

- 当前 state 与 Transition sequence/Artifact；
- `default_contract`；
- maintenance、shadow runtime、v0.21/v0.22 创建权限。

维护态下该接口和 `/api/v2/health` 均返回 `context.read_only = true`。OpenAPI contract
已经重新生成并纳入一致性测试；新增 `scripts/export_openapi.py` 作为可重复生成入口。

## 后续 M8

下一切片处理受信任 actor 上下文与 operator-only 运维边界，消除 mutation 请求体自报
`researcher_key` / `actor_key` 的授权歧义；之后串联 backup/restore/recovery drill CLI 与最终
release runbook。
