# 候鸟 v0.22 标普 500 候选数据池 v5 人工确认报告

日期：2026-08-20
状态：**候选数据池已发布并通过技术闭包；Registry 0.22.3 尚未发布，前端默认数据池尚未激活。**

## 1. 建议结论

候选 v5 已修复旧 v4 的重复拆股复权错误，并在周频、月频 Cohort 与 Runtime 上分别通过严格闭包审计：阻塞项为 0，待排除项为 0。所有进入 `is_selectable=true` 区间的资产均有可用日线、正价格、合法 OHLC、正成交量、连续 504 个可用交易日暖机，以及必要的生命周期/结算闭包。

但这不是完整无偏的历史标普 500 数据库。由于免费数据源缺失，2007 年平均仅覆盖当时成分股的 57.9%，全区间平均覆盖率为 77.0%，到 2026 年提升到 96.6%。因此该版本可以作为“免费数据条件下的统一研究基线”，但必须在研究结果与 Product 页面持续披露历史覆盖不足和回溯价格快照风险。

建议用户在确认本报告后，再执行以下激活动作：发布 Registry 0.22.3，运行一个周频和一个月频的最小确定性 smoke，随后才允许新的前端研究绑定 v5。激活前不运行全量回测。

## 2. 不可变身份

| 对象 | 身份 / 指纹 |
|---|---|
| 股票风险 Dataset v5 | `7b8940ed-85ea-5109-81c2-f8e5d7fffc78` |
| Dataset Artifact | `ddb1446b-a0e3-45ec-8783-5aa798b54758` |
| Dataset binding fingerprint | `de6d608d403ef00a4d2507826dfe595ec297837e4452e905c9db8937dbb1de92` |
| ETF / SPY Dataset v6 | `528886d4-42ed-4564-a079-80379a82812b` |
| Gate4 | `526f32c6-6569-598e-a758-55a1fc82de8c` |
| Gate4 Artifact | `1770cf4f-622e-406c-b708-10ae0bcd1114` |
| Gate4 fingerprint | `8b5003000bb0ad234441dc74d153dab8bfe137073f4318d827dbb06f662f9406` |
| 周频 Cohort v10 | `b39dbaaf-48cd-5e23-b3b2-df77562b5065` |
| 周频 Runtime v10 | `89d05ec9-2f6a-5384-a0b2-d87317feeb0f` |
| 月频 Cohort v10 | `64c0f45c-72e2-587e-b704-385b34c0ffa4` |
| 月频 Runtime v10 | `c2fb75c4-ecb0-5116-aa6b-f35e7875e824` |

Registry `v022_sp500_asset_registry / 0.22.3 / 22004` 仅已在代码中定义，数据库中尚未发布，因此当前候选环境不会自动成为前端默认环境。

## 3. 数据规模与实验环境

### 股票风险池

- 历史 Security 身份：974；身份和历史成员事件均保留。
- v5 实际包含行情的资产：621。
- 曾在 Cohort 中达到可选状态的资产：606。
- 行情行数：3,034,078。
- 覆盖区间：2004-12-31 至 2026-06-30。
- 复权策略：`split_normalized_ohlcv_dividends_backward_total_return_v2`。
- 价格语义：`historical_constituent_pit__frozen_reconciled_retrospective_split_normalized_total_return_prices`。
- `historical_pit_claimed=false`：历史成分事件有冻结证据，但 Yahoo 历史价格是回溯快照，不能声称价格数据本身原生 PIT。

### ETF 与基准

- IWD、IWF、IWN、IWO、SPY，共 5 个资产、27,035 行。
- 同一覆盖区间内零成交量 0、OHLC envelope 错误 0。
- SPY 继续作为独立基准，不占风险候选资产名额。

### 冻结实验环境

- 暖机起点：2004-12-31。
- 统一评价区间：2007-01-03 至 2026-06-30。
- 严格连续暖机：504 个可用交易日；缺失或零量会重置 streak，不能累计凑数。
- 交易成本：单边 5 bps。
- 执行延迟：1 个交易日。
- 周频决策日：1,017；月频决策日：233。
- 两频均有 5,407 个完整日历 session、4,880 个 Cohort eligibility interval；Runtime 冻结 4,884 个 mask interval、7 个生命周期事件和 7 条结算指令。

## 4. 修复内容

### 4.1 重复拆股复权

旧 v4 把 Yahoo 已拆股归一的 OHLC 再乘一次 split ratio，造成 AAPL、AMZN、NVDA 等证券的虚假跳变。v5 不再对已拆股归一价格重复应用 split，split 仍作为公司行动证据保留；现金股息继续进入同口径总收益重建。

回归样例：

| 证券 | 日期 | 修复后相邻日复权收益 |
|---|---|---:|
| AAPL | 2020-08-31 | 3.3912% |
| AMZN | 2022-06-06 | 1.9943% |
| NVDA | 2024-06-10 | 0.7461% |

v5 共含 470 个真实非零拆股事件，剩余 86 个绝对日收益超过 50% 的事件中，0 个发生在拆股生效日；旧重复复权型断点已消除。

### 4.2 缺口、零成交量与身份问题

- 新增 65 个全区间排除决议：15 个严格闭包失败证券，加 50 个行情区间与任何冻结标普成员有效期完全不重叠的身份。
- 继承 prior Gate 的 288 个排除身份，其中 287 个在 primary v3 中本来就没有行情；CVG 的已审核执行日缺口决议继续进入 v5 reconciliation。
- v5 实际物理排除 66 个有行情/有决议的 Security；Gate4 统一披露 353 个历史排除身份。
- 15 个严格失败证券：`COL, ESRX, TWX, PARA, AMCR, GHC, CHD, EP, XEL, CPWR, CNC, CCI, PFG, BKR, FISV`。
- v5 物理表仍保留 3,521 条位于非可选时期的零成交量观察；周/月 exact audit 均证明可选区间内零成交量为 0。非可选观察不会进入信号、决策、执行或持仓估值路径。
- 无法由免费来源可靠补齐的证券没有被物理删除：Security、成员历史、排除原因和证据继续保留，以便未来补源后发布新版本。

### 4.3 生命周期与结算

当前 Runtime 精确冻结 7 个 confirmed 事件：

| Security | 类型 | 生效日 | 结算腿 |
|---|---|---|---:|
| TWX | stock merger | 2018-06-14 | 2 |
| AET | stock merger | 2018-11-28 | 2 |
| ESRX | stock merger | 2018-12-20 | 2 |
| SCG | stock merger | 2019-01-01 | 1 |
| LLL | stock merger | 2019-06-29 | 1 |
| TSS | stock merger | 2019-09-18 | 1 |
| ABMD | cash merger | 2022-12-22 | 1 |

TWX 新增 SEC 8-K 证据，结算为每股 53.75 美元现金加 1.437 股 T。TWX 仍因更早的行情缺口无法满足严格连续暖机，所以保留生命周期证据但从 v5 实验池排除。

## 5. 严格审计结果

周频 Cohort、月频 Cohort、周频 Runtime、月频 Runtime 四份报告结果一致：

- `passed=true`
- blockers：0
- exclude candidates：0
- positive prices：通过
- raw / adjusted OHLC envelope：通过
- selectable volume：通过
- selectable / decision / execution / potential-held path：通过
- strict consecutive warmup：通过
- lifecycle settlement closure：通过

仍有 45 个 Security、86 个绝对日收益超过 50% 的事件保留为 review warning。它们不再与拆股事件重合，不能仅凭阈值判定为坏数据；完整证券与日期清单见 `2026-08-20_sp500_v5_adjusted_return_review.v1.json`。这 45 条底层问题会在周、月两个 Gate evidence 中各出现一次，因此 Gate 的 warning 总数不是独立问题数量。

## 6. 历史覆盖与偏差风险

按周频决策日计算，冻结成员数量稳定在约 500，但可选资产因免费数据缺口明显偏少：

| 年份 | 平均可选资产 | 平均成员覆盖率 |
|---:|---:|---:|
| 2007 | 287.6 | 57.9% |
| 2008 | 298.5 | 60.0% |
| 2010 | 324.8 | 65.2% |
| 2012 | 337.4 | 67.9% |
| 2014 | 353.4 | 70.9% |
| 2016 | 380.6 | 75.3% |
| 2018 | 406.5 | 80.4% |
| 2020 | 429.6 | 85.1% |
| 2022 | 454.3 | 90.2% |
| 2024 | 471.1 | 93.7% |
| 2026 | 486.0 | 96.6% |

全区间平均成员覆盖率为 77.0%；最低单次覆盖率 56.14%，最高 97.22%。这会造成明显的数据可得性/幸存者偏差，尤其会弱化 2007–2012 年早期历史的横截面代表性。统一起点和统一 mask 保证不同策略之间可比较，但不能消除共同数据池自身的偏差。

## 7. 代码与安全边界

- M134：允许旧 v1 与修复后的 v2 reconciliation 语义并存，旧版本可精确回放。
- M135：Cohort 强制 exact price semantics、正价格/成交量、连续 504 个 usable session；不再允许缺失日累计暖机。
- 数据闭包工具可按 exact Cohort 或 exact Runtime 审计，并输出 blocker / exclusion candidate / review 分层。
- Gate4 必须同时持有周频与月频两份 clean closure evidence。
- 工作台候选资产选择已改为 exact active identity：股票只允许 active risk Dataset，ETF 只允许 exact benchmark Dataset；不再按“最大成员数/最新时间”猜 Dataset。
- 旧 Draft 不会静默重绑到新 Registry。
- Registry 0.22.3 已发布并成为唯一完整的 active workspace identity；它精确绑定 risk Dataset v5、benchmark Dataset v6、Gate4，以及周/月 Cohort/Runtime v10。
- 当前资产页的 v0.22 可选集合为 621 只股票和 5 只 ETF。Gate4 的 353 个统一排除身份、本轮 65 个明确修复排除身份与可选集合的交集均为 0。
- 旧失败 Suite 不再因 Runtime 版本升级被后台自动复活；历史失败任务只能显式重试或重新提交，避免污染新数据验证队列。
- Common Evaluation Panel 的大规模成员写入改为保持相同 ordinal/指纹语义的分批写入，消除了逐行数据库 round-trip 的主要瓶颈。
- 没有删除、覆盖或失效 v2/v3/v4、Gate1–3、Cohort/Runtime v9、Registry 0.22.0–0.22.2。

## 8. 验证记录

- 数据治理与发布阶段聚焦 Python 测试：69 passed；激活、Smoke 与运行时收尾测试另有 25 passed。
- Product 前端披露聚焦测试：3 passed；ESLint、TypeScript 通过。
- Ruff：通过。
- strict mypy：15 个相关源文件通过。
- Alembic：唯一 head `20260820_135_v022_cohort_gate`；共享数据库同 revision。
- Registry 0.22.3 已发布：`c8053cc4-b358-52c1-a605-c864a37a3946`。
- 周频真实 Smoke：Suite `02294ccf-b79a-58ed-89d0-e8936f86eca7`，5/5 Work completed，Result accepted、quality passed，区间精确为 2007-01-03 至 2026-06-30。
- 月频真实 Smoke：Suite `9ca5e512-4cd3-5abe-9650-be89e0a146c8`，5/5 Work completed，Result accepted、quality passed，区间精确为 2007-01-03 至 2026-06-30。
- 两次 Smoke 均完成 Processing → Aggregation → Strategy → Portfolio Cell → Evidence Publication，没有触发数据闭包、生命周期、成交量或结算失败。

## 9. 相关证据文件

- `2026-08-20_sp500_primary_v3_pre_repair_closure.v1.json`
- `2026-08-20_sp500_primary_v3_data_repair.v1.json`
- `2026-08-20_sp500_primary_v3_prepared_repair.v1.json`
- `2026-08-20_sp500_targeted_market_repair.v1.json`
- `2026-08-20_sp500_targeted_market_repair_outcome.md`
- `2026-08-20_sp500_cohort_v10_publication.v1.json`
- `2026-08-20_sp500_v5_gate4_publication.v1.json`
- `2026-08-20_sp500_runtime_v10_publication.v1.json`
- `2026-08-20_sp500_v5_weekly_cohort10_closure.v1.json`
- `2026-08-20_sp500_v5_monthly_cohort10_closure.v1.json`
- `2026-08-20_sp500_v5_weekly_runtime10_closure.v1.json`
- `2026-08-20_sp500_v5_monthly_runtime10_closure.v1.json`
- `2026-08-20_sp500_v5_adjusted_return_review.v1.json`

## 10. 已冻结决定与后续边界

用户已确认以下口径并完成激活：

1. v5 是开通 Norgate 等付费 PIT 数据源之前的统一免费股票研究基线；无法可靠修复的身份严格排除，不在运行时自动补齐或缩短回测区间。
2. 接受早期历史覆盖不足带来的共同偏差，并在 Product 页面永久展示免费来源、回溯价格、统一排除和人工修复警告。
3. 保留 45 个 Security 的 86 个真实/待复核大幅波动为 review warning，不因单日复权收益超过 50% 自动删除。
4. 明确异常数据已经从 active v5 研究/选择路径物理排除。旧已发布 Dataset 及其血缘暂不原地删除或改写；后续仅通过依赖感知、可审计的 GC 清理不再被强根引用的旧物理对象。
5. 下一阶段性能重点是 Processing/Aggregation 的分区读取、跨周/月复用和市场面板缓存；本次真实 Smoke 首次物化仍出现约 8.2 GB 峰值内存，但没有影响 v5 数据正确性结论。
