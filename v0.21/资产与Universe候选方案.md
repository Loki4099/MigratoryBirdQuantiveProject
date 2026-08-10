# 候鸟实验室 v0.21：资产与 Universe 候选方案

> 状态：Final 专项方案；受最终 Canonical v0.21 开发方案与《代码对照审查记录》第 18 节约束  
> 日期：2026-08-05  
> 目的：确定以可交易性为目标的股票 Universe、参考指数和资产扩展边界  
> 核心原则：用当时可得数据按规则选择流动性充足的大盘股，不手工挑公司，也不用今天的指数名单回填历史

## 1. 结论

v0.21 大盘股横截面选股的主 Universe 建议采用规则型动态股票池：

`us_liquid_large_cap_300_pit`

它不是固定 ticker 名单，而是在每个季度重构时点，使用当时可得的发行人规模、成交额、价格、上市时间和交易状态，从美国主要交易所普通股中选出约 300 家流动性最强的大盘公司。

选择大盘股的主要目的不是复制某个指数，而是：

- 降低策略交易相对于市场成交量的占比；
- 减少极端买卖价差、停牌和无法成交问题；
- 降低 100M 资金路径的 ADV 参与率与流动性冲击，使容量检验更接近现实；
- 给横截面排序提供足够多、但仍可治理的候选公司；
- 避免手工名单、幸存者偏差和未来信息。

Nasdaq-100、S&P 100、S&P 500、Russell 1000 等保留为成员标签、对照 Universe 或市场参考序列，不作为主 Universe 的唯一成员来源，也不自动进入正式 Benchmark Set。

## 2. 为什么不直接使用“Nasdaq 出现过的所有股票”

Nasdaq Composite 当前覆盖 3,000 多只 Nasdaq 上市股票，历史累计还会包含更多小盘、低流动性、退市和数据不完整证券。它是广泛的 Nasdaq 市场指标，不是大盘股清单。

若使用 Nasdaq-100 历史成员，规模较可控，但会：

- 排除金融公司；
- 只覆盖 Nasdaq 上市公司；
- 带有明显成长和科技行业倾向；
- 把指数提供商的选样目标误当成我们的“低冲击成本”目标。

因此 Nasdaq-100 适合作为对照 Universe 和标签，不适合作为唯一主股票池。主 Universe 应直接优化规模与流动性。

## 3. 主 Universe 定义

### 3.1 候选证券范围

每次重构时从以下证券开始：

- 主要上市地为 NYSE、Nasdaq Global Select Market 或 Nasdaq Global Market；
- 普通股或被批准的普通股 share class；
- 正常交易且有有效日线、公司行动和成交量数据；
- 发行人和证券身份可稳定追踪；
- 美元计价；
- 默认要求美国注册发行人；美国上市的外国公司以后可作为独立扩展。

首版排除：

- ETF、ETN、封闭式基金和共同基金；
- ADR、外国普通股、优先股、权证、rights；
- SPAC unit、空壳公司和 OTC；
- 已进入退市流程或长期停牌的证券；
- 无法获得可靠公司行动或退市处理数据的证券。

### 3.2 硬性可交易性门槛

建议首版门槛：

| 条件 | 建议值 | 目的 |
| --- | ---: | --- |
| 收盘价 | ≥ 5 USD | 排除低价股与异常跳动 |
| 上市历史 | ≥ 252 个交易日 | 保证暖机和基本稳定性 |
| 近 60 日有效交易日 | ≥ 55 日 | 排除停牌或数据断裂 |
| 近 60 日中位成交额 | ≥ 50 million USD/day | 直接控制可交易性 |
| 近 20 日中位成交额 | ≥ 40 million USD/day | 避免近期流动性突然消失 |
| 当时可得市值 | ≥ 10 billion USD | 保持大盘定位 |

这些数字是资产规则的第一版建议，不是策略参数。正式值应在取得真实历史数据后看分布再冻结一次，但不能通过回测收益调优。

### 3.3 排名与成员数量

通过硬门槛后，按以下顺序选择：

1. 以当时可得 free-float market cap 排名；
2. 若没有可靠的 point-in-time free-float 数据，则使用当时总市值；
3. 市值相同或缺少精度时，以近 60 日中位成交额排序；
4. 选择前 300 个发行人。

300 是首版的工程和研究折中：比 Nasdaq-100 或 S&P 100 提供更丰富的横截面，为未来按日期分组的机器学习排序模型提供更多截面样本，同时仍明显小于 Russell 1000，便于维护历史身份、公司行动、因子和信号数据。

不应手工把知名公司加入前 300，也不应因为某股票回测表现好而保留。

### 3.4 Buffer 规则

为减少 Universe 自身产生的无意义换手，采用缓冲区：

- 现有成员只要仍排在前 375 且满足硬门槛，就继续保留；
- 新成员必须进入前 270 才能优先加入；
- 在完成保留和新增后，将成员数补齐或削减到 300；
- 若满足硬门槛的股票不足 300，则允许当期成员少于 300，不降低门槛硬凑数量。

最终排序顺序和边界并列处理必须确定性实现并进入 Universe fingerprint。

### 3.5 重构时间

- 每季度重构一次；
- Rank Date 为 3、6、9、12 月最后一个完整交易周之前的固定交易日；
- 使用 Rank Date 收盘时已经可得的数据；
- 新 Universe 在至少一个完整交易日延迟后生效；
- 重构日历、数据截止时间和生效时间全部版本化。

具体采用季度最后月的第几个交易日，应与 v0.2 的共同交易日和执行引擎对照后冻结。不能为了获得更好收益移动 Rank Date。

## 4. Point-in-time 与数据真实性

主 Universe 每期都必须从当时可得数据重新计算。不能：

- 用 2026 年市值判断 2010 年成员；
- 用今天仍存续的股票回填过去；
- 忽略后来退市或破产的公司；
- 使用后来修订的 shares outstanding 冒充当时数据；
- 因为历史价格缺失而静默删除亏损或退市公司。

Formal 股票链路还必须处理 terminal event：退市、现金收购、换股合并、破产归零等事件必须形成可核验的 terminal total return。没有下一开盘价的失败公司不能从 Universe、Target 或收益路径中静默消失；事件不可核验时，该资产区间阻断 Formal 状态并保留原因。

每个 Universe Publication 应冻结：

- 候选证券快照；
- 所有硬门槛输入；
- 排名值与排除原因；
- 最多 300 个有效成员；
- Rank Date、announcement/evaluation time 和 effective time；
- 市值与成交额计算版本；
- Source Data Bundle 与 Eligibility Version；
- terminal event、terminal return 及其来源/已知时间；
- Semantic Fingerprint 和 lineage manifest。

## 5. 市值数据的现实问题

当前 v0.2 以 Yahoo 固定 OHLCV 快照为主要市场数据。仅靠价格和成交量不能可靠重建 point-in-time 市值，也不能可靠处理退市后的 terminal return，因为还需要历史 shares outstanding、证券母表、股权类别、公司行动与 terminal event 语义。

因此 v0.21 股票池开发前必须选择：

1. 引入能提供 point-in-time market cap / shares outstanding 的数据源；或
2. 若暂时没有可靠市值数据，使用纯流动性 Universe 作为过渡版本，并明确命名为 `us_top_liquidity_300_pit`，不能宣称它是严格大盘股 Universe。

我更推荐方案 1。市值和流动性共同筛选，比复制指数成分更符合降低冲击成本的目标。

## 6. 多股权类别与发行人去重

Universe 目标数量按发行人计算，而不是按 ticker 计算。同一公司的多种股权类别不能占两个 Top-K 槽位。

规则建议：

1. Asset Registry 保存每个可交易 security；
2. 每个 security 关联稳定 `issuer_id`；
3. Universe 排名先聚合到 issuer；
4. 每个季度为发行人冻结一个 `primary_selection_security`；
5. 默认选择近 60 日中位成交额最高且满足全部门槛的 share class；
6. 主证券冻结到下一次 Universe 重构，避免日常切换 share class；
7. 其他 share class 可查看和导出，但不占第二个选股名额。

## 7. 流动性与冲击成本检查

只选大盘股仍不能保证 100M 资金规模可以无冲击交易。正式 Portfolio Backtest 固定使用 100M 初始 Currency Capital，并为每个 Gross Path生成 `base_5bps_plus_impact` 与 `base_10bps_plus_impact` 两条成本路径。容量诊断至少包括：

- 每笔交易名义金额；
- 交易金额 / 20 日或 60 日 ADV；
- 组合最大与中位 participation rate；
- 最大、分位数及超过正式 Capacity Gate 的 ADV 参与率；
- 因成交量不足而需要分日执行的估算；
- 5/10bps 基础摩擦、版本化流动性冲击及Gross路径之间的差异。

成本与容量都不能在资产目录中拍脑袋配置。`Liquidity Impact Model v1`必须版本化冻结公式、波动率估计、系数、上下限和缺失政策。5% ADV与100M资格语义已经按Canonical方案冻结：单笔订单超过5% ADV时，可保留Gross/容量诊断结果，但不得进入Formal排名或升级为Product。Impact公式及系数仍属于P0 Release Gate；系数只能依据执行研究和真实数据画像校准，不得按策略收益调优。

## 8. 对照 Universe

主策略池使用规则型 `US Liquid Large-Cap 300`，同时发布以下只读对照 Universe：

| Universe | 用途 |
| --- | --- |
| Nasdaq-100 PIT | 检查策略在大型成长/科技公司中的表现 |
| S&P 100 PIT | 检查策略在跨行业超大盘蓝筹中的表现 |
| Current Fixed Snapshot | 只用于 UI、数据烟雾测试和前瞻监控，不用于正式长历史声明 |

对照 Universe 需要可靠历史成员才可发布为 point-in-time。若拿不到授权或可核验历史，只保留当前快照，不用其进行正式历史排名。

## 9. 建议新增的参考指数

指数对象与可交易 ETF 必须分开。指数用于市场状态和比较，ETF 才能进入含成本的可执行组合。

### 9.1 第一优先级

| Index | 用途 | 可交易代理候选 |
| --- | --- | --- |
| S&P 500 | 美国大盘市场主基准 | SPY |
| Nasdaq-100 | 大型成长/科技环境 | QQQ |
| S&P 100 | 超大盘蓝筹环境 | OEF |
| Nasdaq Composite | Nasdaq 全市场环境 | ONEQ |
| Russell 1000 | 美国大中盘广度 | IWB |
| Russell 2000 | 小盘相对表现 | IWM |
| Dow Jones Industrial Average | 传统蓝筹市场参考 | DIA |
| Cboe VIX | S&P 500 未来 30 日隐含波动，只作状态数据 | 不把现货 VIX 当作可交易资产 |

### 9.2 第二优先级

- S&P MidCap 400；
- S&P SmallCap 600；
- S&P 500 Equal Weight；
- S&P 500 Growth / Value；
- Nasdaq-100 Equal Weighted。

这些指数先用于基准、状态与页面标签，不自动成为股票 Universe。

## 10. 已确认的 Benchmark Set

大盘股横截面策略的正式 Benchmark Set 固定为：

- Product Primary Benchmark：SPY Buy-and-Hold；
- Research Benchmark：当期 PIT Eligible Universe 同频等权再平衡。

`Universe Equal Weight` 很重要：它帮助区分收益究竟来自模型排序，还是来自入选的大盘股票整体上涨。它只能作为研究 benchmark，不替代 SPY 产品主基准。

QQQ、IWB 与 RSP 继续作为资产目录、市场环境和集中度参考对象，可在资产/参考分析中查看和导出，但不加入正式 Benchmark Set、不新增 Experiment Cell，也不参与6格Qualification拼装。

## 11. 初始资产分层

### 11.1 股票

- 所有曾进入正式 `US Liquid Large-Cap 300 PIT` 候选或成员计算的证券；
- 被排除证券也保留排除原因和当期输入，但不一定长期物化全部因子；
- 历史成员退出后保留 Asset Identity、数据和血缘；
- 不因未来退市而从历史候选中删除。

### 11.2 指数

- SPX、NDX、OEX、IXIC、RUI、RUT、DJIA、VIX；
- 后续可增加 MID、SML、S&P 500 Equal Weight 和 NDX Equal Weight。

### 11.3 ETF

- 保留 v0.2 的 IWF、IWD、IWO、IWN 与 SPY；
- 第一批增加 QQQ、OEF、ONEQ、IWB、IWM、DIA、RSP；
- 多 ETF 轮动使用的行业、地域、债券、商品、防御与国际 ETF 在策略专项另行确定。

## 12. 暂不纳入主股票池

- Nasdaq Composite 全部历史股票；
- Russell 1000/3000 全体历史成员；
- S&P 500 全体历史成员；
- OTC、微盘、低价和低流动性股票；
- ADR 与外国普通股全面扩展；
- 无可靠公司行动、退市和 shares outstanding 数据的证券。

这些不是永久排除，而是避免首版资产治理范围失控。

## 13. 开发前必须验证

1. point-in-time market cap / shares outstanding 数据源；
2. 全候选证券的 OHLCV、公司行动、退市和 ticker 映射覆盖率；
3. 300/270/375 成员与 buffer 规则在历史上的稳定性；
4. 50m/40m ADV 与 10bn 市值门槛在历史各阶段留下的候选数量；
5. 发行人和多 share class 映射；
6. 新资产规模下 Factor、Signal、Model 定向计算性能；
7. participation rate 与现有成本引擎的接口；
8. 指数数据与 ETF 总回报 benchmark 口径。
9. 退市、现金收购、换股合并、破产归零等 terminal event 与 terminal total return 覆盖率；
10. `US Liquid Large-Cap 300 PIT`的精确 Rank Date、data cutoff、publication/effective session 与季度日历；
11. `Liquidity Impact Model v1`的公式、波动率估计、系数、上下限、缺失政策及正式 ADV Capacity Gate。

第1、2、3、5、9、10、11项是Formal Fixture、回归测试和Product开关启用前的P0发布门。它们只用于验证数据可行性、规则稳定性和执行语义，不以策略收益选择门槛或校准参数。

## 14. 已确认的下游策略接口

两个策略都从模型最终、连续且可横截面比较的`model_score`做排序。低分辨率Directional/Voting输出首版不能连接Top-K策略，只进入Predictive Diagnostic。

### 14.1 大盘股横截面选股

- Formal PIT Eligible Universe每个Decision至少100只，50至99只仅Exploratory，少于50只拒绝启动；
- Formal Score Coverage逐Decision至少90%，同时要求`rankable_count >= K`；
- 已发布K为10/20/30，默认20；Equal Slot Weight；
- 已发布Frequency为Monthly/Weekly，默认Monthly；
- Selection Buffer为None/Half-K，Sector Cap为None/风险预算30%；硬约束后不足K则Decision失败；
- Benchmark Set仅含SPY Primary与PIT Universe同频等权再平衡Research Benchmark；
- 100M容量由Experiment的版本化Impact/Capacity Policy验收，不在Universe规则中静默放宽。

### 14.2 多 ETF 轮动

- ETF Family接受任意冻结的合法ETF Asset Set；四ETF仅是默认Sample与回归Fixture；
- Formal分支逐Decision至少2只可排名ETF且Score Coverage至少90%；
- 首版K固定为1，Frequency为Weekly/Monthly；
- 普通Defense Preset只有None/Fixed 20%，动态防御只通过已封装`internal_timing_v1`；
- Benchmark Set仅含SPY Primary与Selected ETF Set同频等权再平衡Research Benchmark；
- 不足绝对数量或覆盖率门槛时Decision失败，Reserve不能掩盖数据或模型故障。

## 15. 官方参考

多资产目录、能力成熟度与防御资产候选见 [`多资产目录与防御资产方案.md`](多资产目录与防御资产方案.md)。

- [Nasdaq Composite 官方说明](https://www.nasdaq.com/solutions/global-indexes/nasdaq-composite)
- [Nasdaq-100 重构与方法概览](https://www.nasdaq.com/articles/global-indexes/2025-nasdaq-100-reconstitution-and-performance-highlights)
- [Nasdaq-100 2026 方法更新](https://www.nasdaq.com/newsroom/nasdaq100-index-methodology-update-why-now)
- [S&P 美国指数方法](https://www.spglobal.com/spdji/en/methodology/article/sp-us-indices-methodology/)
- [S&P 100 官方页面](https://www.spglobal.com/spdji/en/indices/equity/sp-100/)
- [Russell 美国指数方法](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/ground-rules/russell-us-indexes-construction-and-methodology.pdf)
- [Dow Jones Averages 方法](https://www.spglobal.com/spdji/en/methodology/article/dow-jones-averages-methodology/)
- [Cboe VIX FAQ](https://www.cboe.com/tradable_products/vix/faqs)
