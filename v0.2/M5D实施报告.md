# M5D 实施报告：Models 只读 API 与双语页面

## 完成内容

- 新增 `GET /api/v2/models/overview?frequency=weekly|monthly`；
- API 返回最新正式 Model Evaluation 的版本上下文、86 个模型、维度/信号构成、全区间与年度指标、模型对、受控消融和诊断提示；
- 每个模型返回 specification、聚合方法、输出类型、维度权重、信号权重及其独立质量状态；
- OpenAPI 固定契约与 TypeScript 类型同步更新；
- Model domain 在 capabilities 中由 planned 变为 available；
- Models 页面支持周频/月频、模型类型筛选、模型/方法/维度搜索、分批展示和中英双语；
- 维度与 Signal 构成、年度稳定性按模型折叠；75 条全区间消融默认折叠；高相关模型对只显示前 30 条提醒；
- 页面不出现 Sharpe、CAGR、回撤或策略排行榜。

## 验证结果

- API 单元契约与 ETag 路径通过；
- 真实 PostgreSQL API 返回 86 个模型、151 个维度、3,655 个模型对和 150 条全区间/年度消融；
- 前端 TypeScript、ESLint、Vitest 与 production build 全部通过；
- 实际浏览器验证桌面筛选、中文/英文切换和 375px 响应式布局，无横向溢出；
- 全量真实 PostgreSQL 回归：`155 passed`。

## 下一步

M6 进入策略层：先冻结三种横截面轮动策略变体的产品契约、模型兼容边界、调仓/过滤/执行职责与版本身份，再实现目标仓位生成。策略层开始聚合前置研究对象，但实际净值、成本和区间绩效仍由后续 Experiment 运行回答。
