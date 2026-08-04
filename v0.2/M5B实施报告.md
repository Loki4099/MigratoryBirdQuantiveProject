# M5B 实施报告：Model 计算与 Dataset 发布

## 交付结果

本阶段把 M5A 的不可变规格应用到准确的 Signal Dataset，形成可供后续诊断和策略使用的 Model 输出。

- 新增无数据库依赖的纯 Model 计算器；
- 支持两级 `weighted_mean`、`majority_vote` 和 `weighted_vote`；
- 每个 Signal 完成自身暖机后取 specification 共同起点，共同起点后的缺失直接失败；
- 禁止动态丢弃缺失输入或对剩余权重静默重新归一化；
- vote 先对维度输出取 sign，再生成归一化投票余量；平票为 neutral；
- 新增版本化 Model Engine，冻结权重、投票、暖机、量化、方向和归约顺序；
- 新增 Model Dataset、component-to-Signal-Dataset 显式映射和 Model Value；
- input-set hash 允许同一 specification 在准确输入集合改变时形成新 Dataset 身份；
- 新增 `model bootstrap-engine` 与 `model publish` CLI；
- 将 Data→Factor→Signal→Model 纳入真实 PostgreSQL E2E。

## 输出语义

`score` 是 `[-1,1]` 的模型输出。连续模型保存加权分数，投票模型保存带符号的归一化投票余量。`direction` 是 score 的正、负或零状态。v1 的 `confidence` 固定为 `abs(score)`，只表示归一化分数强度或投票一致程度，不是收益概率、p 值或统计置信区间。

## 验证结果

- ruff format/check：通过；
- mypy：通过；
- 新增 weighted/vote/warmup/missing/engine/CLI 单元测试；
- 86 个 Model Dataset、331 个显式输入、2720 个 Model Value 的真实 E2E：通过；
- single-signal Model 与原 Signal score 精确一致；
- 重复发布身份和内容完全复用；
- published Dataset/Input/Value 不可修改；
- M0–M5B 完整真实 PostgreSQL 回归：152 passed。

## 下一步

M5C 使用明确的 weekly/monthly Forward Return Dataset 对 Model 进行独立评价，加入输出行为、稳定性、冗余和基于已发布规格的消融比较。
