# M5C 实施报告：Model 独立诊断与受控消融

## 完成内容

- 新增版本化 `model_evaluation_engine`，冻结 Rank IC、Top-Bottom、年度窗口、相关性、输出离散度和消融口径；
- 基于同一资产池、Forward Return Dataset、日期与共同有效样本评价全部 86 个已发布 Model Dataset；
- 保存逐期诊断、全区间/自然年度汇总、模型两两输出相关性、Top-2 重合率与潜在换手；
- 保存分数横截面离散度、非中性比例与平均分数强度；这里的 confidence 仍是 `|score|`，不是概率；
- 对 31 个维度子集等权模型建立完整子集格，并发布“完整子集减去一个维度”与对应正式子模型的受控消融；
- 固定权重和 directional vote 不与等权子模型混做消融，避免同时改变维度与聚合方法；
- 所有评价、比较和告警都属于 Model 研究诊断，不包含策略收益、成本、Sharpe 或回撤。

## 数据与追溯

迁移 `20260804_16_v02_model_eval` 新增：

- `model.model_evaluation`；
- `model.model_evaluation_period`；
- `model.model_evaluation_metric`；
- `model.model_pair_diagnostic`；
- `model.model_ablation_comparison`；
- `model.model_diagnostic_issue`。

每个评价制品显式依赖 Model Catalog、Universe、Data Bundle、Eligibility、Model Engine、Model Evaluation Engine、Forward Return Dataset，以及全部参与评价的 Model Dataset。所有业务表只允许在所属 artifact 为 draft 时写入。

## 验证结果

- 纯计算单元测试覆盖 IC、输出离散度、冗余、受控消融和不完整子集格拒绝；
- weekly/monthly 真实 PostgreSQL 端到端测试各评价 86 个模型；
- 每个频率生成 3,655 个模型对和 150 条全区间/年度消融记录；
- 重复发布复用同一评价 artifact；
- 全量回归：`154 passed`。

## 下一步

M5D 提供只读双语 Models API/UI。页面只展示模型定义、构成、独立诊断、冗余与消融，不引入策略绩效排行榜。
