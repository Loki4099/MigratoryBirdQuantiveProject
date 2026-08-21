# AMENDMENT-004：双频启动、原生分层聚合与训练型模型集成

> 状态：accepted
>
> 批准日期：2026-08-18
>
> 实现状态：completed（2026-08-19，数据库 head `20260819_128_v022_product_state`）
>
> 影响范围：实验启动、聚合编译与运行、训练型模型、结果证据和 Product

本修订冻结本轮短期开发边界。它不改写既有 v0.22.0 历史身份；新增的
Target、Recipe、Model State、Ensemble State 和 Launch Batch 均以追加版本发布。

## 1. 双频实验启动

- 一个 Compiled Graph、Graph Suite 和 Evaluation Cohort 仍只绑定一个频率；
- 用户启动实验时默认选择“周频 + 月频”，也可显式只选一种频率；
- 后端以同一 Draft revision 为源，分别重新派生、预检和编译周频与月频图；
- 两个频率都通过预检后，受控 Launch Batch 才提交子 Suite；
- Batch 冻结源 revision、两张图、两个 Cohort、幂等键和每个子 Suite 状态；
- 周频与月频结果严格分榜，不复制指标，不跨 Cohort 混排；
- 双频暂时串行或限并发执行，禁止两个高内存聚合作业无控并发。

## 2. 聚合候选与共同输入规则

- 只有人工发布并抵达 Stage 3、标记为 `aggregation_ready` 的输出进入候选池；
- Raw 或上游输出不会因“可直通”而自动进入 Stage 3；直通路线也必须由 Catalog
  明确发布；
- 用户选择的完整、有序 Stage 3 输入集合进入每个合法聚合模型；模型不得静默
  增加、删除或替换列；
- 首版训练型模型接受 1—32 个输入；同一 Feature Family 在一张 Graph 中最多选择
  一个参数窗口，参数窗口比较通过独立实验完成；
- `aggregation_ready` 只表示候选资格。每个 Family 仍须验证 scale、direction、
  value kind、PIT、coverage、missing policy 和自己的输入能力契约。

## 3. 原生分层聚合 v2

旧版三个 legacy preset 只允许精确命中冻结迁移配方。Workspace 与 Compiler 必须在
运行前拒绝没有配方的组合，不能继续显示为“全部输入可接受”。

新增原生分层模型的冻结权重语义：

- 有效研究维度之间等权；
- 同一研究维度内部，用户所选合法信号等权；
- 某维度包含更多信号时，不提高该维度总权重；
- `centered_rank` 可直接参与；`event_score` / `state_score` 必须先绑定发布的校准
  或排名转换；
- 编译时发布并冻结完整 Recipe Artifact，包括 input→dimension、组件权重、维度权重、
  scale/direction 语义和 fingerprint；运行时不得从名称猜测。

参数方案 UI 必须明确显示“尚需选择聚合方案”，不能把两步交互误报为缺失 Catalog
参数，也不能静默选择默认 preset。

## 4. 训练型模型与 Target

本轮不发布二分类 Target 或逻辑回归。首批连续横截面模型为：

- OLS Linear Regression；
- Ridge Regression；
- Random Forest Regressor；
- XGBoost Regressor；
- LightGBM Regressor。

新增固定 XNYS session Target Version：H5、H10、H21。语义为决策收盘后，于下一共同
交易日开盘进入，并在恰好 H 个完整交易 session interval 后的开盘退出；每个截面的
合法未来总收益转换为连续横截面 average-rank，并中心化至 `[-1, 1]`。

- H5/H10/H21 使用频率中立的日度训练与预测格点；
- 周频与月频策略读取同一套合法日度模型输出，仅改变调仓时钟、成本和组合路径；
- `next_scheduled_execution` 继续作为独立、频率相关的 Target，不能伪装成 H5/H21，
  也不能跨周/月复用 Model State；
- 评价起点保持冻结，不允许因模型历史不足向后移动；不足时在 admission 阶段失败或
  以已发布研究警告处理，不能静默缩短样本。

## 5. 同模型内部集成与独立模型分支

不同 Model Family 永远形成独立 Aggregation Branch。所有合法选中的模型接收同一份
完整 Stage 3 输入；它们的信号不在本轮做跨算法平均。

同一 Model Family 内可以选择一个或多个 Target 以及一个或多个已发布 Training
Preset。编译器只展开用户明确选择的成员坐标，并预览成员数量和资源成本。

冻结两级等权规则：

1. 同一 Target 内，不同超参数 preset 成员的严格 OOF rank 信号等权；
2. 不同 Target 的组信号再次等权。

每个成员先在共同预测截面 rank-center 到 `[-1, 1]`，组内与组间平均后再做一次
rank-centering，生成该 Model Family 唯一最终信号。不能平铺全部成员直接平均，避免
某个 Target 因选择更多 preset 而获得更高总权重。

- OLS 没有有意义的超参数集成，通常是单成员分支；
- Ridge、Random Forest、XGBoost 和 LightGBM 可发布少量保守 preset；
- 每个模型分支首版最多 12 个内部成员；
- 首版固定等权，不从排行榜、locked test Sharpe 或最终收益学习权重；
- 任一必需成员失败或缺少共同预测面板，该分支失败，不动态删除成员后重归一；
- 内部成员默认不各自产生 Portfolio Cell；用户若要比较成员，须另启独立实验。

## 6. 训练、证据与 Product 身份

训练型聚合必须追加并冻结：Feature Schema、Target、Training/Fold Policy、Base Learner
Spec、Fitted Model、严格 OOF Prediction、Ensemble Spec、Ensemble Prediction 和运行环境
身份。禁止随机拆分；label maturity、purge 和 embargo 使用每条标签的真实 entry、exit
与 known-at 区间。

Product 升级对象是一个模型分支的完整 Ensemble State。重训时所有成员先完成验证，
再原子发布新 Ensemble State；不得混用不同批次的成员。成员失败时保留上一套完整状态
并显示 stale/warning，不回退成少成员模型、等权输入或其他算法。

结果详情至少展示成员 Target/preset、Fold、Rank IC、覆盖率、预测相关性、逐成员消融、
最终 Ensemble 诊断和 Portfolio 曲线。周/月继续在相同冻结环境内独立排行。

## 7. 开发顺序

1. 聚合和策略读取器只展开冻结 Cohort 的真实决策日；升级执行器身份并验证数值等价；
2. 实现受控双频 Launch Batch、双频预检、幂等恢复、双进度和 Cohort 历史选择；
3. 修复 legacy hierarchical fail-fast 与参数方案交互；
4. 发布 taxonomy、Recipe Artifact 与 native hierarchical v2；
5. 实现训练矩阵、Target、Fold、Model State、OOF Prediction 和通用模型适配器；
6. 先完成 OLS/Ridge 垂直切片，再接 RF、LightGBM、XGBoost；
7. 实现同模型 preset/Target 两级集成、结果证据与 Product Ensemble State；
8. 完成真实周/月受控烟测后再开放前端验收。

## 8. 本轮明确不做

- 二分类 Target、Logistic Regression 或分类树；
- 跨算法自动 Ensemble；
- 学习型 stacking、动态权重或基于最终排行榜的成员选择；
- 自动超参数搜索、隐式笛卡尔积或无上限成员展开；
- 为某个模型移动统一评价起点、删除因子或改变冻结 Universe；
- 将多个 Portfolio Cell 的回测结果事后混合成预测信号。

## 9. 实现闭环与验收记录

- M120—M121：受控双频 Launch Batch、严格频率分榜、legacy 配方 fail-fast、原生
  hierarchical Recipe Artifact；
- M122—M127：训练矩阵、H5/H10/H21 Target、时间序列 Fold、Feature Schema、Base
  Learner、Fitted Model、严格 OOF、两级 Ensemble 和诊断证据；
- M128：Promotion 将监督学习实验的完整成员状态原子冻结为 Product Ensemble State，
  Product 决策只读取同一完整状态；确定性 Product 继续保持空 Model State；
- Product 决策执行器身份升级为 `v022-product-decision-runtime-3`，避免复用旧语义缓存；
- 结果详情投影严格 OOF 成员、Target、Fold、Rank IC、预测相关性与留一诊断；Product
  详情投影当前生效的完整 Ensemble State、成员、激活计划日、状态指纹与失败保留政策；
- 2026-08-19 在独立临时 PostgreSQL 中完成空库升级、M128 非空降级拒绝、11 条真实
  周/月模型主链烟测（OLS、Ridge、RF、LightGBM、XGBoost 及 H5/H10×两 preset 的
  四成员 Ensemble）；另有 107 条本轮聚焦单测与 62 条前端组件测试通过；
- 本地服务已升级至 M128，前端生产构建成功；API 实际返回五个监督学习 Family、三个
  Target、对应训练 preset、周/月排行榜及 v0.22 Product catalog。
