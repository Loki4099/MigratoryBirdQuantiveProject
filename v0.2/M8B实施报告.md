# M8B 实施报告：受控比较与决策解释

## 1. 本阶段交付

- Product Compare 支持选择 2–6 个已接受实验结果；
- Controlled Compare 严格判定及普通并排降级；
- 完整指标的并排查看和 undefined 原因保留；
- Decision Explorer 按调仓日展示 Strategy → Model → Signal → Factor → Data；
- 线性加权模型组件贡献和 vote 模型非线性提示；
- 中英文页面、只读 API、OpenAPI 契约和自动化测试；
- 中文品牌从“候鸟研究所”调整为“候鸟实验室”。

## 2. 受控比较解决什么问题

两个回测结果不同，并不意味着某一个参数导致了差异。如果模型、成本和区间同时变化，就无法把绩效差异归因给其中任何一个。

M8B 因此区分：

- Controlled Compare：受保护市场上下文完全一致，且六个可研究维度中只有一个变化；
- Side-by-side：允许阅读，但多个维度或受保护上下文变化，不能作单因素解释；
- Identical：没有可识别的研究维度差异。

六个可研究维度是 model、strategy、K、frequency、cost 和 interval。受保护上下文包括资产池、数据版本、可用性、执行、储备、基准、指标口径、引擎和币种。

这种设计比“页面看起来相近就叫受控”更严格。代价是部分直观比较会被降级，但它能防止过度解释。

## 3. Decision Explorer 解决什么问题

策略收益只能说明最终发生了什么，不能说明某次调仓为什么形成。Decision Explorer 从一个已接受 Result 出发，选择真实 decision date 后依次展示：

1. 每只候选资产的 Model 分数、名次和目标仓位；
2. Model 使用的维度和 Signal；
3. 当日 Signal 分数与状态；
4. Signal 对应的 Factor 原始值；
5. Factor 使用的精确 Data Bundle。

它读取已发布数据，不在浏览器重新计算。这样页面解释与正式回测使用的是同一条血缘。

## 4. 为什么不新增解释表

常见方案有实时只读查询和预先保存解释快照。实时查询不会复制数据，始终跟随不可变上游发布物；快照读取更快，也适合签发正式报告，但会增加存储和发布治理。

当前数据规模较小，且页面用于研究检查，所以 M8B 采用只读查询。若以后需要对外签发报告或查询性能成为真实瓶颈，再增加独立 Report Snapshot。

## 5. 贡献值的边界

Weighted-mean 模型在 identity 维度变换下，可以显示 `Signal 输入 × 组件权重 × 维度权重` 的线性贡献。Majority vote 或 weighted vote 的最终方向经过符号聚合，不能把同一线性数字冒充精确贡献；页面会明确显示“不作线性归因”。

这项区别很重要：能解释的部分应当解释，不能线性分解的模型不应为了界面整齐而伪造精度。

## 6. 如何检查实现是否合理

- Controlled Compare 页面必须显示变化维度或阻断字段；
- 同时改变两个研究维度时不得显示为受控；
- 切换决策日期后，资产、Signal 和 Factor 值必须来自该日期；
- 页面不得发送写请求，也不得在前端计算研究指标；
- Result、Signal Dataset、Factor Dataset 和 Data Bundle 都应能返回对应 artifact 血缘。

M8B 已在真实 PostgreSQL 合成研究链中验证 interval-only 受控比较，并验证两个调仓日各返回四只资产和十六条组件解释链。
