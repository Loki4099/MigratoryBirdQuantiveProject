# 候鸟 v0.22 M4 Evidence 数据库发布检查点

日期：2026-08-11

## 数据库契约

新增 Alembic head `20260811_59_v022_parity` 和 `compatibility` schema：

- `v022_parity_evidence`：79 个具体 Factor/Signal 对象各一条，绑定真实 lineage Artifact、
  精确 Catalog Release、对应 Feature Variant Artifact、两套比较结果和 Evidence document；
- `v022_migration_registry`：聚合发布状态、M0 baseline、Registry/Evidence/runtime fingerprint、
  28/51/158 完整数量；
- `v022_migration_registry_member`：按确定 ordinal 绑定 79 份 Evidence；数据库 trigger 校验
  Registry 与 Evidence fingerprint、身份和 passed 状态一致。

三张表均由 append-only trigger 禁止 UPDATE/DELETE。Registry 状态约束只允许
`parity_passed`，且只有 28 Factor、51 Signal、158 comparison 的完整发布可写入。

## 发布器约束

发布前重新验证：

- Evidence 整体 canonical fingerprint；
- Registry version/fingerprint 与 M0 baseline；
- runtime contract 和固定比较策略；
- 79 个具体身份及 mapping；
- 每个对象两套精确 Oracle Artifact/content hash；
- expected/actual/matched row count 完全一致；
- missing、extra、numeric、state、event mismatch 全部为零。

每份 Evidence Artifact 依赖精确 Catalog Release Artifact 和对应 Feature Variant Artifact；
聚合 Registry Artifact 再依赖 Catalog Release 和全部 79 份 Evidence，形成完整 lineage closure。
发布在单个数据库事务内原子完成，重复调用复用同一批 Artifact，不产生重复记录。

## 验证

- 空测试库迁移至新 head 成功；
- 全量 475-component Catalog 发布成功；
- 首次发布：79 Evidence + 1 Migration Registry + 79 membership；
- 二次发布：79/79 Evidence 和 Registry 全部幂等复用；
- Registry lineage dependency 中包含 79 个 `parity_evidence` 成员；
- 直接修改 Evidence 被 append-only trigger 拒绝。

本检查点只在隔离测试库验证发布流程，不迁移或写入 v0.21 生产 Oracle。下一步继续完成 M4 要求的
增量执行、分片复用和历史修订失效测试；这些通过后再生成 `M4_GATE.md`。
