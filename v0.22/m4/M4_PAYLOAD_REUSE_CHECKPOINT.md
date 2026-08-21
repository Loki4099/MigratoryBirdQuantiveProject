# 候鸟 v0.22 M4 单输出 Payload 复用检查点

日期：2026-08-11

## 本段完成范围

新增 Node Run 单输出发布器，将增量计划连接到 M2 已冻结的共享 Payload 基础设施：

- 执行结果必须是可读取的 canonical Parquet；行数从 Parquet metadata 获取，不信任调用方声明；
- Payload Object 使用原始文件字节的 SHA-256 内容寻址；本地对象存储写入
  `sha256/<hash>.parquet`，数据库记录规范 `payload-object://sha256/...` URI；
- Object 和 Partition 均按内容身份幂等发布，冲突身份 fail closed；
- 每次 Node Run 创建新的不可变 Payload Manifest；
- 新 Manifest 对未变化数据引用已有 Payload Partition，对执行分片引用新 Partition；
- 新 Manifest lineage 依赖精确 Producer Node Run，以及提供复用 Partition 的旧 Manifest；
- Manifest、Manifest-Partition、Node Run Output、分片状态和 Run 完成状态在同一数据库事务写入；
- 数据库提交失败时可能只留下未引用的内容寻址文件，可由既定 GC 流程回收，不会产生已发布的半个 Manifest；
- 完全相同的发布重试返回同一 Manifest，不生成重复 Artifact。

## 集成验证

隔离 PostgreSQL 测试执行两次 Node Run：

1. 首次运行执行并发布 2 个 Partition；
2. 追加运行复用前 2 个 Partition，只执行并发布第 3 个 Partition；
3. 首次与追加运行持有不同 Manifest；旧 Manifest 仍为 2 分片，新 Manifest 为 3 分片；
4. 数据库最终只有 3 个 Object、3 个 Partition、2 个 Manifest；
5. 追加 Run 分片状态为 `reused, reused, completed`；
6. 新 Manifest 对旧 Manifest 建立 `reused_payload_manifest` lineage dependency；
7. 重复发布幂等复用同一 Manifest；
8. 直接 UPDATE 旧 Payload Partition 被 append-only trigger 拒绝。

同时验证内容寻址文件采用原始字节 SHA-256、重复写入幂等，并能检测同一路径上的损坏对象。

验证结果：

- 全量单元测试：361 passed；
- Payload 数据库集成测试：1 passed；
- Ruff、strict mypy 与 `git diff --check` 通过。

## 多输出原子性边界

当前发布器只接受仅有一个 output port 的 Node Version。对 Amihud daily primitives 这类多输出节点，
发布器会在创建 Object、Partition 或 Manifest 之前明确拒绝，并要求使用 atomic output bundle。
这是有意的 fail-closed 边界：逐端口独立提交会允许部分输出成功、部分失败，违反
`atomic_output_quality_gate`。

下一段必须实现同一 Node Run 的多输出联合校验和单事务多 Manifest 发布。完成该测试前不生成
`M4_GATE.md`。
