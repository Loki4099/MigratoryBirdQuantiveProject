# 候鸟 v0.22 M3 后端垂直切片检查点

日期：2026-08-10
状态：`backend_vertical_slice_passed`，`m3_gate_open`

## 已完成

- 发布 Catalog Release `0.22.1`，源码清单哈希为
  `3afc13f1166c602d1e5637a75738748e2920a7fd27c27cd56e0e1b15fd7f39e2`；
- 发布三条代表链：`return_continuation`、`price_cross_above_ma`、
  `low_illiquidity_quality`；
- 发布 7 个 Processing Node Variant、9 个加工 Feature Variant，并保持“一种公式、
  参数不同才属于同一 Family”的身份边界；
- `amihud_daily_primitives` 一次 Node Run 可原子地产生 `simple_return`、
  `dollar_volume`、`daily_price_impact` 三个不同 Feature Family；
- 编译器支持早期层 Feature 逐层投影至节点上一层，不允许物理跳层；
- 最终信号直接选择可反向展开全部人工血缘；同一节点的全部输出只生成一次节点实例；
- 草稿使用局部聚合参数 key，数据库使用带 Family 命名空间的全局 key；二者转换已冻结在
  Catalog loader/persistence 边界；
- 三条代表链具备确定性内存执行闭环，并由 `flat_equal_weight_mean` /
  `signal_equal_v1` 聚合为一个最终信号。

## 验收证据

- Catalog lint：8/8 checks passed，117 components；
- M3 PostgreSQL 集成测试：7 nodes、14 feature occurrences、2 projections、
  1 aggregation instance、1 strategy branch；
- Amihud 多输出：3 个端口分别绑定 3 个 Feature Family/Variant；
- v0.22 graph/catalog/processing unit tests：13 passed；
- Ruff 与 mypy：passed。

## 本检查点未宣称完成的范围

- Graph-aware Workspace API 与 Derived View solver；
- Workspace 新 Shell、双向选择、selected-first、锁定/禁用原因和血缘抽屉；
- Review/导出前端闭环及浏览器视觉验收；
- M3 全量回归与 `M3_GATE.md`。

以上项目完成前，M3 不得标记完成，v0.22 Workspace 不替换 v0.21 默认入口。
