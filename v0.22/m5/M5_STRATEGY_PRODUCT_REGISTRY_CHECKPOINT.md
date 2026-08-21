# M5 Strategy/Defense/Product Registry 检查点

日期：2026-08-11

## Catalog 修正

M0 Oracle 的 14 个历史 Strategy Version 实际分为两个旧资产上下文：

- `multi_etf_top_k`：4；
- `us_large_cap_top_k`：10。

两者计算公式相同，因此按冻结的 Family 定义映射到同一个
`cross_section_rank_top_k` Family；资产范围和参数契约分别由两个 Variant 表达：

- `cross_section_rank_top_k_parity`：ETF，历史 K=1/2；
- `cross_section_rank_top_k_large_cap_parity`：大盘股，K=10/20、`half_k` buffer。

新 Catalog Release `0.22.3` 包含 477 个唯一组件，并已在隔离 PostgreSQL 完成物理发布。
发布器现在对同一 Family 的多个 Variant 只发布/引用一次 Family Artifact，同时拒绝 Variant
之间重定义 Family 语义。

旧 `ma200_defense` Catalog 错写为跌破均线时 100% 防御，与冻结计划及 v0.21 实现不符。
遵守 append-only 规则，没有原地修改旧 Artifact；新 Release 使用
`ma200_tiered_defense`，精确保留 `SPY/SMA200-1` 的三档预算：

- `> +2%`：0%；
- `[-2%, +2%]`：20%；
- `< -2%`：40%。

`fixed20_defense` 升为 version 2，用于声明 ETF 与大盘股两个资产上下文。`none` 继续使用
nullable/no-selection 语义，不制造虚假 Defense Artifact。

## 迁移台账

`strategy-product-migration-registry.v0.22.0.json` 已通过显式 `READ ONLY` 事务冻结：

- 14/14 historical compiled Strategy Version；
- 10/10 被引用的 compiled Model Instance 身份池；
- Defense 引用：none 8、fixed20 6；
- Defense baseline：none、fixed20、MA200 tiered；
- Product Version：1/1；
- active Product：1/1；当前数据库不存在额外 historical Product Version。

active Product `candidate__9232af7f7a3a571b7276f29a` 的展示/追踪链已明确冻结为：

- Aggregation：`flat_equal_weight_mean + signal_equal_v1`；
- 输入 Signal：`low_skew_premium__w60`、`return_continuation__w20`、
  `return_continuation__w60`；
- Strategy：`cross_section_rank_top_k_large_cap_parity`，weekly、K=10、`half_k`；
- Defense：none；
- Artifact closure：218；dependency edges：418，均绑定独立指纹。

Registry 指纹为
`d7977378380fd80a610b14b38f668c0961507f8df22325720ad1cd0b6a6c22e5`。

## Runtime 检查

已增加独立的 v0.22 Strategy/Defense Runtime：

- ETF 与大盘股 Variant 分别校验自己的 K/buffer/sector-cap 契约；
- none/fixed20/MA200 budget 精确回归；
- MA200 在 ±2% 边界保持 20%，缺失 SPY/SMA200 时 fail-closed；
- ETF 与 100 资产大盘股固定回归 Cell 均完成资金守恒检查。

后续 `strategy-defense-product-parity-evidence.v0.22.0.json` 已完成独立固定回归和 active
Product 连续决策验证；统一结论见 `M5_GATE.md`，M5 Gate 已通过。
