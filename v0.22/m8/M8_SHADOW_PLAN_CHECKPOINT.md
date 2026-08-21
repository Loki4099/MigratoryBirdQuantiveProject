# M8 Representative Shadow Plan 检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-11T18:30:00+08:00`

## 1. 冻结身份

新增不可变 `workspace.v022_shadow_plan` 和有序 `workspace.v022_shadow_representative`。每个代表项
精确绑定：

- Asset Context key、class 与 Configuration 中的 context fingerprint；
- `weekly` 或 `monthly` frequency；
- Product Enrollment 与其 exact Execution Version；
- `active_product_shadow` 或不驱动正式资金的 `shadow_only` 角色；
- weekly 12 期、monthly 3 期的独立最低 prospective session 数。

`drives_formal_capital` 在数据库固定为 false。Shadow 双跑只能产生比较证据，不能借代表性身份驱动正式资金。

## 2. Fail-closed 约束

- 一个 Plan 的每个 `(asset_context_key, frequency)` 必须且只能有一个 Representative；
- 同一 Enrollment 或 Execution Version 不能填充同一 Plan 的多个 Context 槽位；
- Enrollment、Execution 与 Configuration 必须已发布且彼此精确归属；
- frequency 与 asset context fingerprint 必须匹配冻结 Configuration；
- context key 与 class 必须从同 fingerprint 的冻结 Graph Draft Asset Context 反查；ETF/large-cap
  不能由调用方自行贴标签冒充；
- Plan 的 context matrix 与有序成员必须完整，发布后父子记录均不可修改；
- Plan 可以作为渐进 Wave 只覆盖部分 Context，但部分 Plan 不构成 default coverage evidence。

因此 ETF、large-cap 和 monthly shadow cell 可以逐步注册，但最终 coverage Gate 必须从冻结 Plan 明确检查
缺失槽位，不能调用方口头宣称“已覆盖”。

## 3. 当前边界

本检查点只冻结观察对象，不累计 session、不比较 v0.21/v0.22 决策，也不签发
`shadow_coverage_artifact_id`。下一切片将发布逐 session 双跑 Comparison 与按 Representative 独立计算的
Coverage Snapshot。

## 4. 验证

- Python unit：399 passed；
- 全 v0.22 PostgreSQL integration：21 passed；
- 完整 Product fixture 下 Shadow Plan 发布、幂等重放和 frequency mismatch 拒绝：1 passed；
- Ruff、strict mypy、revision 68 空库/增量迁移和 `git diff --check` 通过。

当前测试 Plan 只有一个 weekly ETF 代表项，因此被如实记录为不覆盖 large-cap、monthly；它不会被后续
Coverage Service 误判为 default-ready。
