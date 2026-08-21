# M5 Model/Aggregation Parity 检查点

日期：2026-08-11

## 结论

已实现 v0.22 四个确定性 Aggregation Family 的纯计算内核，其中本检查点原生执行了旧
Model Catalog 实际使用的三个 Family：

- `single_signal_identity`：51 个配方；
- `hierarchical_weighted_mean`：33 个配方；
- `directional_weighted_vote`：2 个配方。

`flat_equal_weight_mean` 同时具备运行实现，但它属于 Workspace/Product 的直接信号等权
语义，不伪装成 86 个旧 Model Specification 之一。

## 执行契约

1. Runtime 只执行 Model Migration Registry 中显式声明的 overall method、dimension
   method、input transform、weight、输入顺序和 Family/Preset，不根据类型猜测配方。
2. 组件内先按冻结顺序加权并 Q18 half-even；维度输出再执行声明的 transform；最终按
   Family 完成分层加权或 directional vote，并再次 Q18 half-even。
3. 多输入采用 v0.21 的 common-warmup/complete-case 契约；warmup 后缺少任意输入立即失败，
   不做隐式填充。
4. parity 输入来自已经通过 M4 parity 的冻结 v0.21 Signal Dataset 边界，输出对照 M0
   冻结 Model Dataset。这样单独衡量 aggregation 差异，不重复混入 raw→signal 误差。
5. Oracle 连接使用 `REPEATABLE READ, READ ONLY` 事务，Evidence 生成后回滚事务；没有改写
   v0.21 数据。

## 验收结果

- Model Specification：86/86 passed；
- 两套 frozen bundle：172/172 comparisons passed；
- 实际逐点行数：1,864,416 actual rows 与 1,864,416 Oracle rows 完整匹配；
- score mismatch：0；
- direction mismatch：0；
- confidence mismatch：0；
- missing/extra row：0；
- 比较政策：Decimal Q18 exact，不使用近似容差；
- Evidence：`model-parity-evidence.v0.22.0.json`，带独立内容指纹并 fail-closed 验证。

因此 M5 的“86 legacy Model exact mapping + parity”子项已完成。M5 Gate 尚未通过；下一项是
精确盘点并迁移 Strategy、Defense 与历史/active Product 引用链。
