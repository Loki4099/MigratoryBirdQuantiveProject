# M7C 实施报告：Benchmark Path

## 交付结果

M7C 实现三类正式基准，并让它们完整复用 Target → Gross → Net Cost 会计链：

- `SPY Buy-and-Hold`：唯一产品基准；
- `Four-ETF Equal-Weight Buy-and-Hold`：研究基准；
- `Four-ETF Equal-Weight Same-Schedule Rebalanced`：研究基准。

## 关键结构调整

早期数据库中的 `benchmark_target_path` 只能保存一个 benchmark asset，无法表达四 ETF 篮子。本阶段增加 Benchmark Definition/Version、Benchmark Decision 和 Benchmark Asset Position；基准目标不再借用包含 model score/rank 的策略仓位表。

每条 Benchmark Target 引用一条已发布参考 Strategy Target，从而锁定相同 Universe、Data Bundle、Eligibility、Execution Policy、Rebalance Schedule、共同起点和 simulation end。Benchmark Target Engine 独立版本化，数据库同时阻止 benchmark engine 与 model strategy target 混用。

## 会计语义

SPY 和四 ETF 买入持有均在参考策略第一项决策对应的下一共同交易日开盘建仓，之后不主动调仓，期末不清算。同频等权在参考策略每个决策日重新产生四只 ETF 各 25% 的目标。

Gross 和 Net 不使用基准专用算法：它们读取相同 adjusted OHLC、reserve interval、下一交易日开盘执行政策和 Accounting Engine。各 benchmark 按自身交易产生 2/5/10 bps 成本，不复制主动策略换手。

## 验证结果

- 178 个单元测试通过；
- Ruff 与 strict Mypy 通过；
- PostgreSQL migration 空库升级/降级通过；
- 三类 Benchmark Target 幂等发布并生成对应 Gross Path；
- SPY 仅执行一次初始买入，标准化资产交易比例为 1；
- 四 ETF 买入持有只建仓一次，同频等权决策数与参考策略一致；
- 三条 Gross Path 均成功展开 2/5/10 bps，共九条 Benchmark Net Cost Path。

下一阶段 M7D 将实现多区间结果和正式绩效指标。
