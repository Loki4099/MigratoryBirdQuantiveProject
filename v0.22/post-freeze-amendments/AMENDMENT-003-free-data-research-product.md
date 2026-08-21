# AMENDMENT-003：免费多源数据与带警告的研究 Product

> 状态：accepted
>
> 批准日期：2026-08-17
>
> 影响范围：v0.22 数据源、人工修复证据、Dataset 质量门禁、排行榜和 Product 准入

本修订更新 [`AMENDMENT-002`](AMENDMENT-002-sp500-cohort-results.md) 中将
`exploratory_only` 与 Product 升级绝对绑定的规则。用户确认：在使用免费数据的
前提下，Product 可以接受已冻结、可解释、可重放的数据质量问题，但必须在
Product 页面持续显示相应警告。

## 1. 两类准入独立判定

1. `ranking_eligibility`
   - `rankable_research`：可进入同一 Evaluation Cohort 的严格可比排行榜；
   - `exploratory_only`：只进入独立的探索结果列表，不与主榜混排。
2. `product_eligibility`
   - `eligible`：数据证据完整，无警告或只有展示性口径说明；
   - `eligible_with_warnings`：可创建研究 Product，但必须冻结并显示警告；
   - `ineligible`：存在会改变计算身份、持仓估值或结算结果的未解决问题。

`ranking_eligibility` 与 `product_eligibility` 是两个不同的冻结结论。一个
`exploratory_only` Result 在通过计算正确性门禁后，仍可以是
`eligible_with_warnings` 并升级为研究 Product。

## 2. 可以以警告形式接受的问题

以下问题不单独阻止研究 Product：

- 历史 S&P 500 成分来自回溯重建，而不是数据商原生 PIT 快照；
- 后复权价格来自冻结的 retrospective snapshot；
- 使用多个免费数据源和经人工审核的缺口修复；
- 已有明确 Evidence 和确定性 policy 的 provider gap、停牌、普通调出、
  现金并购、换股并购、分拆、退市或 OTC 迁移；
- 对所有策略一致、已冻结且数量可见的 provider-unavailable 排除；
- 备用源与主源在重叠区间有可量化差异，但已通过固定 reconciliation policy
  产生唯一 Canonical Dataset。

## 3. 仍然必须阻止 Product 的正确性问题

以下不是“免费数据警告”，而是计算正确性阻断：

- ticker reuse 或并购重组后仍无法确定稳定 Security 身份；
- 已有持仓在停牌、退市、并购或分拆时没有确定估值/结算路径；
- 缺失 Benchmark、Calendar、统一评价区间或完整的 decision-session panel；
- 为某个策略临时删股、填值或改变数据源，导致策略之间环境不一致；
- 回测时联网搜索、按结果临时修数据，或手工覆盖原始 Parquet/数据库事实；
- 无法重放得到相同 Dataset、Mask、Result 和 Product Qualification。

## 4. 免费多源数据路线

1. 历史成分：现有 MIT S&P 500 历史快照，以 S&P 公告、SEC 和公司文件作为
   关键事件证据；
2. 稳定身份：SEC CIK + OpenFIGI + provider-scoped ticker intervals；
3. 2004–2018 早期行情候选：先对 Nasdaq Data Link `WIKI/PRICES` 做覆盖审计，
   不在审计前直接发布；
4. 现存行情：复用本地已冻结的 Yahoo/Tiingo 对象，逐对象核验 provenance
   和使用范围；
5. 身份与生命周期证据：SEC EDGAR、OpenFIGI、Nasdaq Trader、FINRA OTC、NYSE/公司公告；
6. Yahoo/yfinance 不再被定义为唯一权威源。新的自动抓取必须有可证明的许可；
   无许可的页面抓取不得成为发布主链。

多源数据不能直接混合各家 adjusted close。主链优先冻结 raw OHLCV 和 corporate
actions，用同一确定性规则重建后复权序列；备用源只能补经审核的精确区间。

## 5. 人工修复与运行时边界

- 系统按 `source_symbol × effective interval` 生成身份审查队列；
- 系统自动生成缺 bar、ticker 边界、连续 gap、异常末日、企业行动冲突、退市和
  持仓结算审查队列；
- 人工只处理异常项，每个结论发布 append-only Evidence/Resolution Artifact；
- 人工结论不得覆盖原始事实；必须通过新 Dataset Release 生效；
- 已冻结的 Graph Suite/Portfolio Runtime 不联网，不根据运行结果修数据。

历史成员 mask 必须覆盖每个 decision session 的全部合法候选，不能只检查最终
Top-K。事件处理还必须覆盖两次调仓之间的已有持仓。

## 6. Product 展示和持续监控

所有 `eligible_with_warnings` Product 必须展示：

- `research_product`，不声称机构级 provider-native PIT；
- historical membership 与 retrospective price 语义；
- Dataset/Universe/Eligibility/Gate fingerprints；
- 人工 resolution 数量与类型；
- 每个备用源、补段数量、统一排除数量和已知偏差；
- 当前 warning/blocker 列表；
- 每次未来 Product Decision 实际使用的 Dataset/Universe/Manifest Artifact。

警告不阻止 Product 运行；新的正确性 blocker 出现时，必须停止新决策并保留
旧决策与证据。

## 7. 开发顺序

1. `M102`：Identity Review Case + Evidence；
2. `M103`：Lifecycle/Tradability Event + Settlement Leg；
3. `M104`：备用源 Observation、Gap Resolution 与 deterministic reconciliation；
4. `M105`：Dataset Gate Assessment，独立冻结 ranking/product eligibility 及统一排除；
5. `M106`：Evaluation Cohort v2，让 Planner/Runtime 真正消费成员、可选择、可交易、
   估值与 settlement 状态；
6. `M107`：Product Data Disclosure、带警告的 promote-and-enroll 及未来输入发布策略。
7. `M110`：为每个 Product Enrollment × Decision Session 发布唯一、不可变的
   Product Input Snapshot。它显式依赖 Enrollment、Product Data Disclosure、
   Dataset、Universe History、Calendar 与 Dataset Gate；允许同一冻结方法论和
   数据系列的后续发布版本，但禁止运行时联网、静默缩短区间或替换历史证据。
8. `M111`：增加 Product 专用运行输入覆盖层。新的 Raw Manifest、Processing、
   Aggregation、Strategy、Defense 与 Merge 必须从 M110 Snapshot 出发；不得把新
   Dataset 伪装成原 Compiled Execution Context 的旧 Dataset。Product Decision
   必须依赖实际使用的 Product Input Snapshot 和对应 Manifest。

`M111` 分两段交付，但只有两段都完成后才算可运行：

- `M111-A`：冻结每个 Product Input Snapshot 在决策日的 exact Universe Snapshot、
  全部成员、504-session warm-up 状态、uniform exclusion/terminal 状态，并建立
  Product Snapshot 专用 Raw Payload Manifest binding；
- `M111-B`：让 Product 专用 Processing/聚合/策略/防御/合并执行器只读取上述
  binding，并把 Product Input Snapshot + Manifest 纳入 Work 与 Product Decision
  fingerprint/lineage。不得以旧 Suite 的 compiled context binding 代替。

`M111-A` 完成不代表 Product Worker 可运行；在 `M111-B` 完成前不得启用或回退旧
Product Decision 执行路径。

`M110` 只冻结输入身份，不授权旧 Suite Runtime 读取不同 Dataset。`M111` 完成前，
Product Worker 对缺少可执行 Snapshot 的到期 session 必须显式缺失/停止，不能回退到
推广时的历史 Dataset，也不能在运行时抓取网络数据。

第一版数据治理交互先使用“导出审查清单 → 人工填写 JSON/CSV → CLI 校验并发布
Evidence”，不先扩展成复杂管理前端。

## 8. 最小验收

- Nasdaq WIKI 对 974 个历史 source symbol 的覆盖报告和 2013–2018 重叠期差异报告；
- rename 与 ticker reuse 均通过区间身份解析；
- Yahoo/主源缺段经备用源审核后能生成新 Dataset，不覆盖旧版；
- confirmed halt 不与 provider gap 混淆；
- 普通调出后不能新开仓，但已有持仓可按规则平仓；
- 现金并购、换股并购和分拆各有一条确定性结算回归；
- 所有 Cell 消费相同 decision-session mask；
- 质量警告不阻止 `eligible_with_warnings` Product，正确性 blocker 仍会阻止；
- Product 页面完整显示数据来源、修复、排除、警告和冻结指纹。

本修订不修改 v0.21 语义，不覆盖旧 Dataset/Result/Product。新状态、Artifact 和门禁
必须通过 append-only migration 与新 Contract Version 实施。
