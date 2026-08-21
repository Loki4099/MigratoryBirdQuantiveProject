# M7 Research Configuration Snapshot 发布检查点

日期：2026-08-11
状态：`passed`（M7 子检查点；M7 Gate 仍开放）

## 权威来源

`ConfigurationSnapshotService` 只接收一个已编译的 Strategy Branch、冻结 execution policy 和
provenance。Aggregation、参数轴、Strategy、Defense 与 direct Stage 3 inputs 全部从 PostgreSQL 的
Compiled Graph/Catalog identity 表读取；调用方不能提交名称、信号列表或版本号来重新解释历史配置。

direct inputs 按 `workspace.compiled_aggregation_input.ordinal` 冻结，并同时记录：

- Feature Family / Variant / Version identity；
- Compiled Feature Occurrence 与 Stage 3 output port；
- origin stage、projection/source occurrence；
- Payload Contract Version 与 schema fingerprint；
- 发布时的名称和参数展示副本。

## 三类文档

- `semantic_identity_document`：Graph、Asset/Data Binding、frequency、Aggregation axes、有序输入、
  Strategy、Defense 和 execution policy；只对它计算 configuration fingerprint；
- `provenance_document`：Graph Draft/revision、Suite/branch 等来源；不参与语义 fingerprint；
- `display_document`：发布时的名称、版本号和参数摘要；不从 latest Catalog 动态重建。

同一 semantic configuration 再次发布会返回既有 Snapshot。后续请求携带不同 provenance/display
不会覆盖首次冻结内容；execution/cost policy 等语义改变则产生新 fingerprint 与新 Snapshot。

## Artifact 与幂等性

Snapshot 使用 content-addressed configuration fingerprint 作为 Artifact key，并依赖精确 Compiled
Graph Artifact。发布事务同时写入 Snapshot、全部 direct input 行、Artifact 状态与 Lineage Manifest；
任一步失败整体回滚。并发复用后会重新读取数据库中的冻结文档，不返回竞争请求的临时 provenance。

## 验证

- Python unit：385 passed；
- PostgreSQL Graph Draft integration：5 passed；
- 验证 3 Aggregation instance / 6 branch 图中的具体 Branch 可发布 Snapshot；
- 验证 direct input ordinal、名称、Variant、幂等 provenance 和 execution-policy identity 分叉；
- Ruff 与 strict mypy 通过。

下一检查点实现 Common Evaluation Panel 与 Result Evidence 的发布服务，然后才能构建 Comparison 和
matched baseline；当前不宣称 Experiment Result 第一屏或 Product runtime 已完成。
