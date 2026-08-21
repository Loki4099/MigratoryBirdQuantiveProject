# M5：Model、Strategy 与 Defense parity

M5 按冻结计划完成 86 个 v0.21 Model Specification、Strategy/Defense 运行约束以及
历史与 active Product 引用链迁移。

本目录当前已冻结第一项输入台账：

- `model-migration-registry.v0.22.0.json`：86 个 Model Specification 的精确配方、
  v0.22 Aggregation Family/Preset 映射、输入 Signal Variant 与 172 个 M0 Oracle 输出；
- `M5_MODEL_REGISTRY_CHECKPOINT.md`：抽取边界、验证结果和下一步工作。
- `model-parity-evidence.v0.22.0.json`：86 个配方在两个冻结 bundle 下的 172 次逐点比较；
- `M5_MODEL_PARITY_CHECKPOINT.md`：确定性 Aggregation Runtime 和零差异验收结果。
- `strategy-product-migration-registry.v0.22.0.json`：14 个历史 Strategy、三种 Defense
  baseline 和全部 Product Version 的精确映射及 active Product 血缘闭包；
- `M5_STRATEGY_PRODUCT_REGISTRY_CHECKPOINT.md`：Strategy Family/Variant、MA200
  append-only 修正、运行适配器和 Product 引用结果。
- `strategy-defense-product-parity-evidence.v0.22.0.json`：两个 Strategy、三种 Defense
  固定回归，以及 active Product 102,916 个聚合分数和 1,042 个目标权重决策的连续性 Evidence；
- `M5_GATE.md`：M5 的统一 Gate 结论、独立性边界、冻结指纹与 M6 入口。

M5 Gate 已通过：86/86 Model、172/172 Model 比较、两个 Strategy、三种 Defense 与唯一
active Product 均无未解释差异。后续从 M6 Workspace 全量前端继续。
