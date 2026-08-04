# M9B 实施报告：完整回测恢复、性能与数据库备份

## 交付结论

M9B 修复了一个发布层缺口：M7 的后端服务虽然已经能够计算完整实验，但正式 CLI 只暴露到 Benchmark Target。数据库被集成测试重建后，用户只能看到 Strategy Target，看不到 accepted Experiment Result、排行榜和 Decision Explorer。

新增 `experiment run-release-cell` 后，一条命令可从指定的已发布 Strategy Target 出发，固定并发布 Accounting、Benchmark、Performance 和 Orchestration Engine，2/5/10 bps 成本模型、SPY 产品基准、Metric Catalog、Atomic Specification、Suite、accepted Result、统一暖机政策与严格 Comparison Cohort。命令不会猜测应代表发布版本的策略目标，调用者必须显式提供其 artifact ID。

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
