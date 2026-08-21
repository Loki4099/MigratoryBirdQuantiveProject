# M5 Model Migration Registry 检查点

日期：2026-08-11

## 结论

已从 M0 冻结的 v0.21 PostgreSQL Oracle，以显式 `READ ONLY` 事务抽取并冻结全部 86 个
Model Specification 的实际维度、组件、转换和权重。事务在读取完成后回滚，未修改 v0.21
生产数据。

映射分布为：

| v0.21 specification type | 数量 | v0.22 Aggregation Family |
|---|---:|---|
| `single_signal` | 51 | `single_signal_identity` |
| `dimension_subset_equal_weight` | 31 | `hierarchical_weighted_mean` |
| `fixed_weight` | 2 | `hierarchical_weighted_mean` |
| `directional_vote` | 2 | `directional_weighted_vote` |

合计 86 个记录、172 个冻结 Model Dataset Oracle 引用。运行器实现前的复核发现首版台账
未显式保存 overall/dimension method，因此已从 method definition 关联表补齐原始字段；最终
台账指纹为 `8ca8e816c7084eddb9b6d48883ed720a87e5e9f97c03fe47823a821943081003`。

## 已冻结边界

1. 86 个旧 Model Specification 是 4 个确定性 Aggregation Family 下的具体配方，不是
   86 个新 Family。
2. `flat_equal_weight_mean` 未被强行映射到上述 86 个配方。它承载 Workspace
   `signal_equal_v1` 和 active Product 的信号等权语义，后续在 Product 引用链检查点处理。
3. 每个输入均指向 M4 已冻结的 stage-3 Signal Variant；没有自动生成或随机线路。
4. 每个记录绑定两个 M0 Oracle 输出，并逐字段验证 artifact、bundle、coverage、row count、
   engine、universe、`input_set_hash` 与内容指纹，任一漂移均拒绝加载。
5. v0.21 的三维等权值按有限精度保存，10 个配方的维度权重和为
   `0.999999999999999900`。Registry 保留原值，并仅对维度权重和冻结
   `1e-16` 的最大容差；组件权重仍要求精确等于 1。该容差不是运行结果 parity 容差。

## 验证

- 独立离线 `--verify`：通过；
- 86/86 legacy key 覆盖：通过；
- 分布与 Family/Preset 合法性：通过；
- Signal Variant 输入完整、唯一且可解析：通过；
- 172/172 Oracle 引用逐字段一致：通过；
- 特殊配方 `trend_tilt_v1` 与 `five_dimension_weighted_vote_v1`：已锁定回归断言；
- 权重或 Oracle 内容被篡改时：验证器 fail-closed。

## 下一步

确定性 Aggregation Runtime 与 172 次 Oracle parity 已在后续检查点完成。下一项迁移
Strategy、Defense 与 Product 引用链；这些完成之前不发布 M5 Gate。
