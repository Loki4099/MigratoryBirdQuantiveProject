# 候鸟 v0.22 M4 多输出原子发布检查点

日期：2026-08-11

## 数据库契约

新增 Alembic head `20260811_60_v022_bundle`：

- `processing.node_output_bundle`：一个 Node Run 只允许一个不可变输出 bundle，记录 bundle
  fingerprint、输出数量和精确 lineage Artifact；
- `processing.node_output_bundle_member`：按固定 ordinal 绑定全部 output port 与各自 Payload
  Manifest；
- 两表均由 append-only trigger 禁止 UPDATE/DELETE；
- 空库升级、降级至 base 后重升均通过，仍保持唯一 Alembic head。

## Runtime 契约

- 调用方必须一次提交 Node Version 声明的全部 output port，缺失或额外端口均 fail closed；
- 所有端口的 Parquet、执行分片和复用分片在进入数据库事务前完成校验；
- 每个端口拥有独立 Payload Manifest、Payload Contract 和历史 Partition 映射；
- 多输出增量复用必须按 `(output_port_key, partition_key_hash)` 提供历史 Partition ID，禁止让
  不同输出共享一个含义不明的旧 Partition ID；
- 全部 Manifest、Run Output、bundle membership、lineage、分片完成状态和 Node Run 完成状态在
  同一事务发布；
- 单输出 API 已改为 atomic bundle 内核的便捷封装，不存在两套发布语义；
- bundle 和各 Manifest 均支持内容与依赖完全一致时的幂等重试。

## 故障与回滚验证

以真实三输出 Amihud daily primitives Node Version 验证：

- 只提交一个端口时，在创建任何数据库输出前被拒绝；
- 三个端口全部合法时，原子生成 3 个 Manifest、3 个 Node Run Output 和 1 个三成员 bundle；
- 相同 bundle 重试复用原 Artifact 与 bundle identity；
- 一个端口提供损坏 Parquet 时，数据库保持 0 Manifest、0 Run Output、0 bundle，Run 仍为
  `running`；
- 注入“第一个 Manifest 已写、第二个 Manifest 抛错”的事务中途故障后，第一个 Manifest、
  Run Output 和全部 bundle 数据均回滚，Run 仍为 `running`。

内容寻址文件在数据库事务前写入；失败可能留下无数据库引用的对象文件，但不会留下已发布的半成品
Artifact，后续由既定 GC 处理。

## 验证结果

- 全量单元测试：361 passed；
- M4 收口 PostgreSQL 回归：7 passed；
- Ruff、strict mypy、`git diff --check` 通过；
- 正式 v0.21 Oracle 未迁移、未写入。
