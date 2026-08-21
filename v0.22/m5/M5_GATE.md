# M5 Gate：Model、Strategy、Defense 与 Product parity

日期：2026-08-11  
状态：`passed`

## Gate 结论

M5 冻结范围已经全部通过，且没有使用近似容差或未解释差异：

- 86/86 legacy Model Specification 完成显式 Aggregation Family/Preset 映射；
- 两个 frozen bundle 共 172/172 Model 比较通过，1,864,416 个输出点完全一致；
- ETF 与大盘股两个 Strategy Variant 均通过固定回归；
- none、fixed20、MA200 tiered 三种 Defense 语义均通过精确预算回归；
- 唯一 active Product 的 102,916 个聚合分数和 1,042 个周度目标权重决策连续；
- mismatch、missing、extra 和 unexplained difference 均为 0。

## 独立性边界

v0.22 Strategy Runtime 自己实现 eligibility、coverage、competition rank、buffer、边界选择、
slot allocation 与资金预算，不在验收时调用 v0.21 Strategy calculator。旧数据库仅作为一次性只读
Oracle：Evidence 生成在 `REPEATABLE READ, READ ONLY` 事务内完成并回滚，同时校验 M0 已冻结的
Artifact ID 与 content hash。

active Product 的连续路径为：

`三条最终 Signal -> flat_equal_weight_mean/signal_equal_v1 -> weekly large-cap Top-K K=10/half_k -> Defense none -> 目标权重`

逐期验收同时比较资产、competition rank、Decimal target weight、defense budget 和 reserve weight。
历史分数在 binary64 排序层发生碰撞时，已显式冻结 `asset_key` 次级排序，避免输入行顺序造成
非确定性换股。

## 冻结 Evidence

- Model Registry：`8ca8e816c7084eddb9b6d48883ed720a87e5e9f97c03fe47823a821943081003`；
- Model parity Evidence：`6034518b9539682a8334b4e5736f9cfa4aefaf897ebcdc59cc069f92e151396f`；
- Strategy/Product Registry：`d7977378380fd80a610b14b38f668c0961507f8df22325720ad1cd0b6a6c22e5`；
- Strategy/Defense/Product Evidence：`f6116b380aadaab952ca3b95c6840c5738dce227e2d40be0c45c35750bb894cd`。

Evidence 验证器均 fail-closed：Registry、上游 Evidence、摘要、逐项 pass 状态或内容指纹任一漂移，
验证即失败。

## 下一里程碑

M5 Gate 已关闭。下一阶段按冻结计划进入 M6：Workspace 全量前端，包括大 Catalog
分页/搜索、selected-first、双向选择、血缘点亮、资源准入，以及 Aggregation/Strategy/Defense Review。
