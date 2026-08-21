# 候鸟 v0.22 M4 Gate

> Contract：`bird-migration-v0.22.0`  
> Milestone：`M4 — 28 Factor / 51 Signal 全量迁移与增量运行闭环`  
> 状态：通过  
> 完成时间：`2026-08-11T10:01:23+08:00`

## 1. 全量兼容身份与 Catalog

- 冻结 28 个具体 Factor Variant 与 51 个具体 Signal Version，共 79 个迁移对象；
- v0.21 的同公式不同参数继续归属同一 Family，未按下游用途错误合并 Family；
- 80 个兼容 Processing Node 部署到三个人工加工层，所有输入边均来自人工 Catalog binding；
- Raw/中间 Feature 通过合法 Projection 逐层抵达后续层，不随机生成加工线路；
- 完整 Catalog Release 包含 475 个 component，Migration Registry 当前版本为 `0.22.3`。

## 2. 独立逐点 parity

- 28/28 Factor Variant 通过；
- 51/51 Signal Version 通过；
- 两套冻结数据上下文共执行 158 个独立比较；
- missing/extra key、numeric、state、event mismatch 全部为零；
- 79 个对象分别拥有不可变 Evidence Artifact，并由一个 append-only Migration Registry 原子聚合；
- Registry 状态只有在 79 份 Evidence 全部存在且通过时才能写入 `parity_passed`；
- Evidence fingerprint：
  `c2b8276e9c7874251b14ff580a003a31a88707b65ed9c1f885b827f3b9abff9f`。

## 3. 增量、修订与不可变 Payload

- Runtime 不推断增量能力；Node Version 未声明时固定 `full_recompute`；
- 显式 `windowed` 契约支持 session-based lookback/lookforward、尾部增量和未变化分片复用；
- 历史修订只失效读取窗口相交的分片，并只向受影响的 downstream 分片传播；
- 每次执行创建新 Manifest，未变化输出引用旧 Partition，旧 Manifest/Partition 不原地修改；
- Payload Object 使用原始文件字节 SHA-256 内容寻址，并验证真实 Parquet metadata；
- 单输出和多输出统一经过 atomic output bundle；全部端口联合提交，任一端口失败全量回滚；
- 多输出历史复用使用精确 `(output_port, partition)` 身份，不共享模糊的旧分片引用。

现有 v0.21 兼容 Catalog v0.22.2 的 80 个 Node Version 仍保守声明为 `full_recompute`。M4
证明的是 Runtime 增量契约与复用/失效行为正确，不声称未经逐族研究的旧节点已经被悄悄增量化；
后续只能用新的 Node Version 发布经研究确认的 `windowed` 能力。

## 4. 数据库与 lineage

- 唯一 Alembic head：`20260811_60_v022_bundle`；
- 新增不可变 parity Evidence/Registry 与 Node Output Bundle/Member 契约；
- Payload Manifest 精确依赖 Producer Node Run，并在复用时依赖旧 Manifest；
- Output Bundle Artifact 依赖全部端口 Manifest，形成从 bundle 到具体 Partition/Object 的闭合血缘；
- 空库升级、降级至 base、重新升级通过；正式 v0.21 Oracle 写入数为 0。

## 5. 验证结论

- 全量单元测试：361 passed；
- M4 收口 PostgreSQL 回归：7 passed；
- 79 Evidence 数据库发布、幂等复用与 append-only 防护通过；
- 增量尾部、历史修订、downstream 传播、单输出 Partition 复用通过；
- 三输出联合发布、幂等重试、损坏输入零发布、中途故障全事务回滚通过；
- Ruff、strict mypy、`git diff --check` 通过；
- 两个无关 v0.1 用户文档未修改、未暂存。

因此 `m5_entry_allowed=true`。下一阶段按冻结计划进入 M5：86 个 legacy Model exact mapping、四个
确定性 Aggregation Family、两类 Strategy、none/fixed20/MA200 Defense，以及历史/active Product
引用链 parity。M4 Gate 不授权提前切换 v0.21 默认入口或 Product。
