# M4B 实施报告：Signal 确定性计算与 Dataset 发布

## 交付结论

M4B 已把 M4A 的 51 个 Signal versions 接入精确的已发布 Factor datasets，生成连续分数、阈值状态和穿越事件，并以原子、不可变、可追溯的 Signal datasets 发布。当前阶段不计算 forward return、IC 或策略收益。

## 已交付

- migration `20260803_11_v02_signal_data`；
- `signal.signal_dataset`、`signal.signal_value` 与 `signal.signal_quality_issue`；
- 独立版本化 `signal_engine`，冻结代码、依赖锁、schema、配置和数值环境；
- 横截面居中平均排名，四资产输出 `-1、-1/3、+1/3、+1`；
- 连续信号在排名后应用 `higher/lower is better` 方向；
- 按目录精确执行阈值边界、true/false score 和穿越条件；
- `NUMERIC(24,18)` Signal score，不用二进制浮点保存最终排名；
- missing、neutral 与 event absence 分离；
- `style-rotation signal bootstrap-engine` 与 `signal publish`；
- 51 个数据集原子发布及重复运行完整复用。

## Candidate 与 Benchmark 边界

Factor Dataset 保留 Universe 中 candidate 与 benchmark 的测量值，方便后续研究诊断。但横截面 Signal 只在 `candidate` 成员之间标准化，SPY 不参与四只风格 ETF 的排名。Signal Dataset 仍绑定完整 Universe 版本，因此候选范围与 benchmark 身份都可追溯。

## 穿越事件首日

Factor Dataset 的正式覆盖从 requested start 开始，没有额外保存 start 前一日值。穿越信号无法判断区间首日是否刚刚发生交叉，因此从第二个有效日期开始发布；首日不会被写成 `event=false`。连续和阈值信号仍从首日开始。

## 数据库保护

数据库触发器验证 Signal Version 与 Factor Dataset 使用同一 Factor Variant，Universe/Data Bundle/Eligibility 上下文一致，并且计算引擎确实为 Signal Engine。值表同时强制连续、阈值和穿越三种输出契约；数据集发布后不可修改。

## 验证

合成端到端测试发布 39 个连续、4 个阈值和 8 个穿越数据集。八个正式交易日、四个 candidate 资产共生成 1600 条 Signal values；51 个数据集各保存 6 条直接血缘依赖，重复发布全部复用。

## 下一阶段

M4C 将建立版本化 forward-return target 和 Signal evaluation，完成 IC、Top-Bottom、区间稳定性、事件/集中度及相关性诊断，再接入 Signals API 与中英双语页面。
