# M9B 实施报告：完整回测恢复、性能与数据库备份

## 2026-08-05 最终完成结论

纠偏后的正式发布已完整物化，不再停留在 90 单元 canonical 样本：

- 35 个产品模型，其中 31 个维度子集等权、2 个 fixed-weight、2 个 directional-vote；
- 3 个策略语义、K=1/2/3、周/月频，共 630 条已发布 Strategy Target；
- SPY Buy-and-Hold、2/5/10 bps、full/trailing 10/5/3/1-year，共 9,450 个 Experiment Specification；
- 9,450/9,450 accepted 且 eligible，0 excluded；每个结果保留 36/36 个 defined 指标；
- 90 个严格 Comparison Cohort，每个 105 个成员，总成员数 9,450；
- 1 年区间的 1,890 个结果明确标为 `short_sample_warning`，仍保留完整 251 个共同交易日，不伪装成长样本；
- 旧 90 单元 canonical Suite、单模型批次和进程池冒烟 Suite 均通过 invalidation 退出默认展示，历史记录未删除。

正式发布使用 revision `20260805_28_v02_target_engine`。revision 27 将 K 与频率加入 Cohort 类型化上下文和数据库触发器；revision 28 移除阻止新 Target Engine 合法重发的过强唯一约束。正式 Suite 新增受限的 1–16 进程并行与按 Target 交错调度，默认仍为单工作者；每个 Experiment Specification 的身份、计算引擎和结果语义不因调度变化而改变。

发布后全量审计确认：630 条 Target 只绑定一个 Universe/Data Bundle/Eligibility/Target Engine；所有目标权重与 synthetic reserve 精确闭合为 1；所有执行均发生于决策日后的下一合法交易日；五区间、三成本、三种 K 和两种频率无缺格。同一严格 Cohort 的实际指标起点完全一致。边界并列时 K 表示槽位预算，`proportional_share_of_remaining_slot_budget` 可使实际 ETF 数超过 K，且由 `boundary_tie_count` 明确记录，这不是越权持仓。

最终回归包括 213 项 Python 单元测试、19 项真实 PostgreSQL 集成测试、Ruff、Mypy、10 项前端测试、ESLint、生产构建与浏览器交互验收。首页、Experiments 和 Ranking 只展示最终 Suite；90 个队列可切换，默认及抽样队列均为 105/105 可排名。最终 custom-format dump 在代码提交后生成并执行隔离恢复测试，文件继续位于 Git 忽略的 `artifacts/`。

## 2026-08-05 纠偏结论

项目所有者在前端验收中发现：数据库只有一个结果，“全历史”实际仅覆盖约两年。复核确认 `run-release-cell` 只是一条纵向冒烟路径，不能作为正式参数空间已完成的证据；原 M9 完成判断撤回。

本轮新增 `strategy publish-grid` 和 `experiment run-release-suite`。后者按显式 Strategy Target 集合展开 3 种成本和 full/trailing 10/5/3/1-year 五个 carry-in 区间，历史不足结果保留为 excluded，eligible 结果按严格上下文自动分组。修复了旧 CLI 使用非正式 `recent_*` 区间键、不同发布域共用一个版本号，以及 10 bps 实盘长小数写入时 NUMERIC(38,24) 独立舍入导致成本对账约束失败的问题。

当前正式展示数据使用 2006-08-07 至 2026-08-03 的 XNYS 可验证市场历史，253 个共同交易日暖机后研究起点为 2007-08-08。首批 canonical 套件包含一个五维等权模型、三种策略、K=2、周/月频、三种成本与五个区间，共 90/90 accepted、90/90 eligible、30 个严格 Cohort，每个 Cohort 有 3 个可比策略变体。旧两年单格与短历史语义套件已通过可追溯 invalidation 退出默认展示。

以上是纠偏过程中的阶段性结论；最终完成状态以本报告顶部“最终完成结论”为准。

## 原 M9B 交付背景

M9B 修复了一个发布层缺口：M7 的后端服务虽然已经能够计算完整实验，但正式 CLI 只暴露到 Benchmark Target。数据库被集成测试重建后，用户只能看到 Strategy Target，看不到 accepted Experiment Result、排行榜和 Decision Explorer。

`experiment run-release-cell` 仍保留为单格冒烟与故障恢复工具，但不再作为正式发布完成入口。

## 已验证的真实结果

- 数据：2023-01-03 至 2026-08-03 的已发布 Yahoo/FRED 市场链；
- 产品：周频 Momentum/Trend 等权模型 + 趋势过滤 Top-2；
- 市场假设：SPY Buy-and-Hold、单边 5 bps、full-history carry-in；
- 结果：`eligible`、`normal`，首次完整发布约 5 秒；
- 前端：Experiment Overview、Result Detail、Product Ranking 和 Decision Explorer API 全部返回 200；
- 浏览器：中文页面显示 36 项完整绩效指标、9 条运行事件、3 项质量检查与逐日组件解释链。

这是一条真实历史模拟结果，不是测试 mock；它当前只覆盖一个发布单元，因此证明完整链路可用，不代表 v0.2 已经跑完正式参数空间的全部组合。

## 备份与恢复

revision `20260805_26_v02_backup` 新增 `ops.backup_record`，保存 system version、schema revision、Git commit、custom-format dump SHA-256、存储位置、字节数与 verified/restore-tested 状态。

`backup create` 使用 PostgreSQL custom-format dump，先写临时文件，验证 `PGDMP` 文件头和 SHA-256 后原子替换正式文件。`backup restore-test` 创建项目限定名称的隔离临时数据库，执行 `pg_restore --exit-on-error`，核对 restored schema revision，随后强制删除该临时数据库。任何失败都会写入 backup record，不能伪装成 restore-tested。

本轮实际生成并恢复验证了约 6.1 MB 的 v0.2 dump；备份位于 Git 忽略的 `artifacts/`，不提交仓库。

## 应用端理解

“测试通过”和“用户能恢复研究结果”不是同一件事。单元与集成测试证明模块正确，但集成测试会主动重建测试库；若没有正式编排入口，测试反而会清空页面所依赖的发布结果。v0.2 因此把开发测试库（`postgres-test`, 55432）与日常展示库（`postgres`, 5432）分开：测试可以反复重置，展示库通过受控发布或已验证备份恢复。
