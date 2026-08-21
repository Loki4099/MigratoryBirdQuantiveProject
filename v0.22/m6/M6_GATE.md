# 候鸟 v0.22 M6 Gate

> Contract：`bird-migration-v0.22.0`
> Milestone：`M6 — Workspace 全量前端`
> 状态：通过
> 完成时间：`2026-08-11T13:55:00+08:00`

## 1. Gate 结论

M6 冻结范围已经全部通过：

- Raw Input + 三个固定加工层 + 一个 Aggregation 层完整可见；
- 上游正向选择约束下游合法性，最终 Stage 3 信号可反向点亮并锁定人工血缘；
- selected/required Family 前置，Catalog 搜索、筛选、分页和逐层导出可用；
- Draft revision、checkpoint/clone、Catalog rebase、跨标签同步和原子 FIFO mutation queue 闭合；
- Aggregation preset、Strategy 和 Defense 均为显式配置，并准确展开独立实验分支；
- Review 显示直接信号、配置身份、branch/cell 数和资源准入结果；
- 大 Catalog 首屏有界，路由拆包和键盘对话框验收通过。

## 2. Fail-closed 边界

- 下游仍依赖时，上游不能静默取消；必须先预览并确认级联影响；
- 唯一 Aggregation preset 也不会隐式选择；
- 不兼容输入、Strategy 频率、资源超限或陈旧 revision 均阻止编译；
- Catalog rebase 不猜测替代退出的 Feature/Aggregation/Strategy/Defense；
- 一个编译分支始终只有一个 Aggregation instance、一个 Strategy、零或一个 Defense；
- 多标签 revision 冲突暂停本地队列，重新加载后才恢复。

## 3. 验证结论

- Python unit：385 passed；
- Frontend：30 passed；TypeScript、ESLint、production build 通过；
- 全 v0.22 PostgreSQL integration：19 passed；
- Ruff、strict mypy、OpenAPI committed contract 和 `git diff --check` 通过；
- production main chunk 364.74 kB，Vite oversized chunk warning 已消失；
- 两个无关 v0.1 用户文档未修改、未暂存。

因此 `m7_entry_allowed=true`。下一阶段按冻结计划进入 M7：Experiment/Product 的
Configuration、Evidence、matched baseline、版本身份与 deterministic continuous decision 路径。
M6 Gate 不授权切换默认入口；v0.22 UI 继续保持非默认，直到 M8 shadow/cutover 完成并由用户实际验收。
