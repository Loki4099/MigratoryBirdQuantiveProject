# M4C2B 实施报告：Signal 只读 API 与双语页面

## 交付结果

本阶段把 M4C2A 已发布的 Signal Evaluation 安全地交付给应用端，完成 M4 的纵向闭环。

- 新增 `GET /api/v2/signals/overview`，要求显式指定 `weekly` 或 `monthly`；
- API 只解析最新 published evaluation，并返回 Signal Catalog、Universe、Data Bundle、Eligibility、Forward Return Dataset 与两个 Engine 的准确 artifact 身份；
- 返回 51 个 Signal 的定义、经济解释、方向、输出类型、全区间指标和年度稳定性；
- 返回 score 相关性、Top-Bottom spread 相关性、Top-2 重合率及质量问题；
- Signal 能力在 capabilities 中从计划状态改为 available；
- 更新 OpenAPI 固定契约及自动生成的 TypeScript 类型；
- 新增中英文 Signals 页面、weekly/monthly URL 状态、质量状态、血缘入口和响应式样式；
- 页面只展示 Signal 自身诊断，不展示 Sharpe、CAGR 或策略排名；
- 更新首页能力边界，将下一阶段指向 Model Library。

## 验证结果

- ruff format/check：通过；
- mypy：通过；
- Python：123 passed；
- ESLint、TypeScript：通过；
- Vitest：6 passed；
- Vite production build：通过；
- OpenAPI committed-contract test：通过。

由于开发者当前无法启动 Docker，16 项需要 `STYLE_ROTATION_TEST_DATABASE_URL` 的 PostgreSQL 集成测试被正常跳过。其中已经接入真实发布链的 Signals API E2E 断言将在 Docker 可用后补跑；这不是以 mock 替代数据库验收。

## 下一步

进入 M5 Model 纵向交付。开始改动 Model schema 前，先依据既定职责边界细化 dataset、输入映射、聚合语义和独立诊断的最小实现切片。
