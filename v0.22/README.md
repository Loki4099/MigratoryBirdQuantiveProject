# 候鸟 v0.22 文档入口

## 当前状态

- 冻结契约：`bird-migration-v0.22.0`
- 状态：`frozen`
- 冻结时间：`2026-08-10T16:29:50+08:00`
- 当前里程碑：M0；M0 Gate 通过后进入 M1。

## 规范性文件

1. [`候鸟v0.22最终开发计划.md`](候鸟v0.22最终开发计划.md)：冻结实施基线。
2. [`contract-decisions.v0.22.0.json`](contract-decisions.v0.22.0.json)：冻结机器可读决策表。
3. [`freeze-manifest.v0.22.0.json`](freeze-manifest.v0.22.0.json)：文件 hash 与冻结验证记录。

候选证据永久保留为 [`候鸟v0.22实施基线_rc1_候选记录.md`](候鸟v0.22实施基线_rc1_候选记录.md) 和 [`contract-decisions.v0.22.0-rc1.json`](contract-decisions.v0.22.0-rc1.json)。

规范文件互相冲突或 Freeze Manifest 校验失败时必须停止实现。冻结后的语义变化只能通过 ADR 和新 Contract Version，不能原地解释或覆盖。

## 非规范性参考文件

其余文件用于保留讨论过程、专项分析和历史设计。它们可能包含已被冻结基线修正的表名、状态、里程碑或能力范围，因此只能作为背景材料，不能直接生成 DDL、API、Catalog 或前端状态机。

常见已废弃内容包括：

- `processing.payload_contract_version`：由共享 `data.payload_contract_*` 取代；
- Processing `one_of/optional` binding：v0.22.0 只允许人工冻结的 `required` binding；
- 无条件 `Aggregation × Target × Preset`：改为按 Family capability 展开；
- `dimension_equal_hierarchical_mean`：改为 `hierarchical_weighted_mean`；
- `directional_majority_vote`：改为 `directional_weighted_vote`；
- `horizon_sessions` 作为唯一 Target：改为 horizon tagged union；
- Node Run 直接归属单个 Compiled Graph：改为 reusable Run + Graph Run Binding；
- LightGBM/RV20 阻塞 v0.22.0：分别移动到 v0.22.1/v0.22.2；
- 51 Signal + 四 Workspace 模型即“全兼容”：改为 86/86 legacy Model Specification 映射与 parity。

## 开发入口

当前先完成 M0，随后按实施基线第 13 节执行：

```text
M0 frozen contract + v0.21 oracle + budgets/gates
→ M1 Payload + 全 Catalog Identity
→ M2 Graph Core + 最小 deterministic runtime
→ M3 代表链 + 前端可见切片
→ M4 51 Signal
→ M5 86 Model + Strategy/Defense parity
→ M6 Workspace 全量前端
→ M7 Product 连续运行
→ M8 v0.22.0 shadow/cutover
→ M9 v0.22.1 LightGBM
→ M10 v0.22.2 RV20
```
