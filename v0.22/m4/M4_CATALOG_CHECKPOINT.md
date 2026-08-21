# 候鸟 v0.22 M4 全量兼容 Catalog 检查点

日期：2026-08-11

## 已完成

- 发布 Catalog 源版本 `0.22.2`，保留 M3 的 7 个节点身份并新增 73 个兼容节点。
- 完整 Processing Catalog 共 80 个 Node、82 个 Feature Output：
  - 28 个 v0.21 Factor Variant；
  - 51 个具体 Signal Version；
  - 3 个 Amihud 显式日频 primitive output。
- 连续、threshold state、crossover event 分别绑定明确兼容算法；每条输入 binding 均来自
  人工冻结的 legacy Factor→Signal recipe，不进行随机组合。
- Raw/Factor/Signal 只按相邻层进入下游；较早产生的最终 Signal 通过 Projection 到 Stage 3，
  不制造伪计算节点。
- 同一 Node/Feature Family 的 name、algorithm/formula、input role、stage 与输出语义保持稳定；
  参数窗口只形成 Variant，不拆成新 Family。
- 全部 51 个 Signal 可同时作为 `flat_equal_weight_mean` 的显式 Stage 3 输入，无 blocker。

## 严格校验发现并保留的修正

全量生成过程中，既有契约依次拒绝了三类错误映射：

1. Amihud Factor 在 Stage 2，依赖它的 Signal 不能仍放在 Stage 2；
2. M3 已冻结的 Return Continuation Family 位于 Stage 3，其他窗口不能漂移到 Stage 2；
3. M3 已冻结的 Amihud Factor Family 位于 Stage 2，w60 不能漂移回 Stage 1。

已提交的错误 Registry `0.22.0` 保留审计；未发布的中间候选只记录错误、不保存完整重复
文件。stage 修正后的映射版本为 `0.22.1`；执行 recipe 补全后，当前有效 Registry 为
`0.22.2`，fingerprint：
`3da4379a2c249fec3c46db4e10d70715d09445415012b87e91fdb418b7b88afe`。
本次补全不改变任何 v0.22 identity、stage、Oracle binding 或 Catalog 节点。

## 验证

- Catalog/Registry/既有 contract 专项：11 passed；Ruff、mypy 通过。
- PostgreSQL 空库发布 475 个不可变 Catalog Component 成功。
- 真实 Compiler 将全部 51 个 Signal 编译到 Stage 3；Compiled Graph 包含 80 个节点、
  1 个 Aggregation Instance 与 1 个 Strategy Branch。
- 生成器 `--verify` 同时校验 Registry、Processing Catalog 和 Release Manifest 可确定性重建。

## 尚未完成

- 73 个新增节点的计算 runtime 与逐点 v0.21 Oracle parity；
- 增量执行、分片复用和历史修订失效；
- Registry/Evidence 的不可变数据库发布与 M4 Gate。

因此本检查点只证明“完整人工 Catalog 可发布、可编译”，不把新增记录提升为
`executable` 或 `parity_passed`。
