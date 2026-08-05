# M9B 实施报告：完整回测恢复、性能与数据库备份

## 2026-08-05 纠偏结论

项目所有者在前端验收中发现：数据库只有一个结果，“全历史”实际仅覆盖约两年。复核确认 `run-release-cell` 只是一条纵向冒烟路径，不能作为正式参数空间已完成的证据；原 M9 完成判断撤回。

本轮新增 `strategy publish-grid` 和 `experiment run-release-suite`。后者按显式 Strategy Target 集合展开 3 种成本和 full/trailing 10/5/3/1-year 五个 carry-in 区间，历史不足结果保留为 excluded，eligible 结果按严格上下文自动分组。修复了旧 CLI 使用非正式 `recent_*` 区间键、不同发布域共用一个版本号，以及 10 bps 实盘长小数写入时 NUMERIC(38,24) 独立舍入导致成本对账约束失败的问题。

当前正式展示数据使用 2006-08-07 至 2026-08-03 的 XNYS 可验证市场历史，253 个共同交易日暖机后研究起点为 2007-08-08。首批 canonical 套件包含一个五维等权模型、三种策略、K=2、周/月频、三种成本与五个区间，共 90/90 accepted、90/90 eligible、30 个严格 Cohort，每个 Cohort 有 3 个可比策略变体。旧两年单格与短历史语义套件已通过可追溯 invalidation 退出默认展示。

这仍不是全部 35 个默认产品模型及 K=1/3 sensitivity 的完整物化，因此 v0.2.0 尚不能冻结。

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
