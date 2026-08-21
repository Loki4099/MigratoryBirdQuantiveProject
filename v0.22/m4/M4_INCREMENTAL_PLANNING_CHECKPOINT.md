# 候鸟 v0.22 M4 增量分片规划检查点

日期：2026-08-11

## 本段完成范围

新增通用 `IncrementalExecutionContract` 与增量分片规划器，执行模式只允许由已发布的
Node Version 显式声明：

- `full_recompute`：即使存在完全相同的历史分片，也必须重新执行；
- `windowed`：只有分片键和扩展读取窗口内的全部 source revision 均未变化，才允许复用；
- `lookback` / `lookforward` 均按实际 session axis 计算，不按自然日估算；
- `windowed_forward`、`same_cross_section` 与
  `from_revised_session_forward` 有各自明确的历史修订传播边界；
- 新尾部分片读取必要 lookback，但输出范围仍裁剪到新分片，不重复发布旧输出。

Planner 只生成不可变计划，不修改旧 Manifest 或旧 Payload Partition。复用结果明确携带旧
`payload_partition_id`，供后续新 Manifest 引用同一不可变分片。

## 数据库约束

新增 `record_partition_plan`，原子写入已有 `processing.node_run_partition`：

- 只允许给 `running` Node Run 写计划；
- 运行时计划必须逐字段匹配该 Run 绑定的已发布 Node Version execution contract；
- 调用方不能把已发布为 `full_recompute` 的节点临时改成 `windowed`；
- 完全相同的计划重试幂等复用；部分写入或冲突计划 fail closed；
- 执行分片记录为 `planned`，复用分片记录为 `reused`。

## 验证结果

- 新增尾部：两个旧分片复用，只执行一个新分片；
- windowed 历史修订：只失效读取窗口相交的当前及后续分片；
- downstream 传播：上游未变分片与对应下游分片均复用，只有上游变化分片及其下游重算；
- forward 修订策略：修订点之前复用，修订点所在及之后分片失效；
- `full_recompute`：匹配历史指纹仍全部执行；
- PostgreSQL 集成：计划首次原子写入、相同重试幂等、越权开启 windowed 被拒绝；
- 全量单元测试：359 passed；本模块增量测试：6 passed；数据库集成测试：1 passed；
- Ruff 与 strict mypy 通过。

## 保守兼容边界

已发布的 v0.21 兼容 Catalog v0.22.2 中 80 个 Node Version 当前全部声明
`full_recompute`。本段不修改不可变 Catalog，也不声称这些节点已经增量化。之后只有在逐族确认
算法窗口、状态与修订影响后，才能用新的 Node Version 发布 `windowed` 契约。

## 尚未完成

M4 Gate 暂不生成。下一段需要把已执行分片发布为不可变 Payload Object/Partition，并让每次
Node Run 创建新的 Payload Manifest；新 Manifest 对未变化分片引用旧 Partition，对重算分片引用
新 Partition。完成真实 Manifest 复用及数据库集成测试后，才能关闭 M4 的增量执行门槛。
