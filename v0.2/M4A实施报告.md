# M4A 实施报告：Signal 定义与受控变换地基

## 交付结论

M4A 已将 M0 的 27 个 Signal 生成模板物化为 51 个正式 Signal definitions 及各自的 v1。每个正式信号引用一个精确 Factor Variant，并显式保存经济解释、方向、标准化、极值、缺失、并列、输出形式和可选规则。当前阶段不计算 Signal values，也不提前加入 IC 或策略收益。

## 关键建模决定

27 个模板不是 27 个可被模型直接引用的信号。模板与多个 Factor Variant 展开后得到的 51 个对象才具有独立研究身份。例如 5 日与 252 日 continuation 是并行存在的不同 Signal definitions，不是同一对象的历史 v1 与 v5。未来修改 252 日 continuation 的规则时，才在该 definition 下产生 v2。

`dimension_hint` 只保留在 M0 生成目录中，不物化为 Signal 的永久维度。实际维度归属将在 Model specification 中确定，避免 Signal 被错误绑定到单一模型结构。

## 已交付

- migration `20260803_10_v02_signal_core`；
- `signal.signal_definition` 与 `signal.signal_version`；
- 51 个独立 signal keys、51 个 v1、41 个 product-eligible 标记；
- 连续、阈值状态、穿越事件和近期事件四种契约；
- 连续信号默认横截面居中排名 `[-1, 1]`，并列使用平均排名；
- 状态/事件信号不伪装成横截面排名，明确使用自身 rule；
- 正式缺失策略为 common warmup 后报错，不把 missing 变成 neutral；
- 每个 Signal version 对 Signal definition 与 Factor Variant 各保存一条直接血缘；
- `style-rotation signal bootstrap` 幂等物化入口；
- 目录内容哈希核对、数据库冻结约束、契约和集成测试。

## 预测期限处理

Signal version 保存 `explicit_evaluation_target_required`。这表示信号假设必须在评价时绑定一个明确的、版本化 forward-return target，而不是由定义层暗中固定周频或月频。后续 M4C 的评价结果将同时追溯 Signal、目标收益口径、共同样本和区间。

## 下一阶段

M4B 将建立 Signal Engine、Signal Dataset 与 Signal Value，消费精确的已发布 Factor Dataset，完成方向变换、横截面平均排名、阈值状态和穿越事件的确定性计算与不可变发布。
