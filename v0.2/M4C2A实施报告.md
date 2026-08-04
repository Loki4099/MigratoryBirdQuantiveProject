# M4C2A 实施报告：Signal 评价诊断后端

## 交付结果

本阶段将 M4C1 的未来收益标签与 M4B 的 Signal Dataset 连接为独立、不可变的研究诊断发布物。

- 新增 Signal Evaluation Engine，冻结 Rank IC、Top-Bottom、IC IR、年度窗口、相关性和短样本语义；
- 新增 `signal_evaluation`、逐期结果、窗口指标、成对诊断和质量问题表；
- 每个评价只绑定一个 weekly 或 monthly Forward Return Dataset，频率不会混排；
- 51 个 Signal 使用相同四只候选 ETF 和共同 decision-date cohort；SPY 标签不参与横截面评价；
- 保存平均/中位 Rank IC、正 IC 比例、年化 IC IR、Top-Bottom、事件率、事件资产集中度、非中性率和潜在 Top-2 换手；
- 保存 full 与 calendar-year 稳定性窗口；
- 保存 score Spearman、Top-Bottom spread 相关性与 Top-2 重合率；
- `|ρ|≥0.85` 只产生提醒，不执行自动删除或准入拦截；
- 新增 CLI、单元测试、迁移链检查及合成 PostgreSQL E2E 接线。

## 质量边界

Rank IC 因信号或未来收益横截面恒定而不可定义时保存空值和稳定原因码，不伪造为零。weekly 少于 12 期或 monthly 少于 6 期时记录短样本警告，但仍保留研究结果。数据库拒绝非法计数、越界比例、非有限浮点和 published 子表修改。

## 下一步

M4C2B 将只读这些已发布结果，完成 Signals API、OpenAPI 类型、双语页面和前端测试；浏览器不重新计算任何统计量。
