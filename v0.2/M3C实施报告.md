# M3C 实施报告：因子诊断、API 与双语页面

## 交付结论

M3C 已完成 Factor 层闭环。系统对同一目录、Universe、Data Bundle、Eligibility、Factor Engine 与共同区间下的 28 个 Factor datasets 发布独立诊断制品，并通过只读 API 和中英双语页面展示因子自身性质。诊断不包含方向、IC 或策略收益。

## 已交付

- migration `20260803_09_v02_factor_diag`；
- `factor_diagnostic_set`、28 条 `factor_dataset_summary`；
- 378 条 `factor_pair_correlation` 与独立诊断问题；
- 均值、样本标准差、最小值、P05、P25、中位数、P75、P95、最大值；
- 对完全相同 asset-date 观测进行平均秩 tie-aware Spearman 相关性；
- `|ρ| ≥ 0.85` 只触发提醒，不自动删除因子；
- 同一 Factor Definition 的不同参数实例单独标记为参数稳定性比较；
- 独立 Factor Diagnostic Engine，记录代码、依赖、schema、配置和数值环境；
- 原子、幂等、不可变诊断发布及 34 条直接血缘依赖；
- `style-rotation factor bootstrap-diagnostic-engine` 与 `factor diagnose`；
- `GET /api/v2/factors/overview`、同步 OpenAPI 和生成的 TypeScript 客户端；
- 双语 Factors 页面、分布条、参数稳定性列表、高相关提醒和诊断血缘入口。

## 诊断口径

相关性使用共同 Universe 和共同有效区间内全部 `asset × observation_date` 对齐值的 pooled Spearman。它回答“两个测量在这个明确上下文中的排序是否相似”，不回答“哪个信号方向正确”或“策略是否赚钱”。

参数稳定性是相关性结果中 `same_definition=true` 的子集。例如 total return 的 20 日与 60 日实例可以比较稳定性；total return 与 realized volatility 即使相关，也属于跨定义冗余诊断。

## 验证

- 迁移可从空库升级、降级后重新升级；
- 合成数据端到端生成 28 条摘要、378 组相关性并重复运行复用；
- 已发布诊断子表无法更新；
- API 不包含 Sharpe 等策略指标；
- 前端 lint、类型检查、组件测试、构建和真实浏览器中英切换均通过。

## 下一阶段

M4 将进入 Signal 层。方向、标准化、极值处理、缺失处理与可选阈值将在 Signal 定义中出现；Rank IC、正 IC 比例和 Top-Bottom 也从这一层开始才具有明确含义。
