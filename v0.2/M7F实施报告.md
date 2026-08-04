# M7F 实施报告：Experiment 只读 API 与双语页面

## 交付结果

M7F 完成了 v0.2 从实验计算链到可检查研究结果的最后一段只读界面。数据库 schema 没有新增迁移，head 仍为 `20260804_24_v02_exp_result`。

- `GET /api/v2/experiments/overview` 返回已发布 Suite 及其有序 Cell，并区分 accepted、failed、running、pending；
- 每个 Cell 显示完整 Strategy Product/Model/Variant、频率、区间模板、产品基准和单边成本假设；
- 核心指标只从 accepted Result 的正式 Metric Value 读取，不为失败或待运行规格制造绩效；
- `GET /api/v2/experiments/results/{artifact_id}` 返回完整绝对/相对指标、实际区间、Run Attempt、质量检查、有序事件和输入输出 Artifact；
- Experiment capability 和 OpenAPI/TypeScript 契约已同步；
- 双语 Experiments 页面提供状态筛选、可比单元表、核心指标和结果审计详情；
- 页面明确说明历史模拟排名的能力边界，不把绩效反写到 Factor、Signal 或 Model 页面；
- excluded accepted Result 保留为正式运行事实，但指标显示为不可用，不缩短请求区间。

## 应用端选择说明

总览采用“Suite 内 Cell”而不是把唯一 Specification 去重后展示。优点是保留研究者明确组织的比较清单和顺序；代价是同一原子规格被多个 Suite 引用时会出现多行。系统用相同 Result Artifact 表明它们共享同一正式结果，这不是重复计算。

失败运行不产生 Result，因此结果详情端点只接受 accepted Result Artifact。失败原因仍在总览 Cell 中保留，完整失败日志由后续 Runs 页面展开。这样可以避免把“运行记录”和“被接受的研究结果”混成同一种对象。

## 验证

- Ruff 与 strict Mypy 通过；
- 后端单元测试 195 项通过；
- 前端 lint、typecheck、9 项组件测试和 production build 通过；
- 真实 PostgreSQL 端到端验证 3 个 Suite、4 个 Cell、eligible/excluded accepted Result、failed attempt、36 项指标、质量检查、事件和输入输出血缘；
- 本地浏览器检查英文页面、真实 PostgreSQL 数据、横向表格、详情指标和运行审计；Sharpe/Information Ratio 等无量纲指标按小数显示，收益与波动率按百分比显示。

## 下一步

M8 将先定义严格 comparison cohort，再实现 Dashboard 核心指标、单指标 Ranking、Compare 与 Decision Explorer。不同成本、区间、频率、基准、数据版本或引擎口径不得直接混排。
