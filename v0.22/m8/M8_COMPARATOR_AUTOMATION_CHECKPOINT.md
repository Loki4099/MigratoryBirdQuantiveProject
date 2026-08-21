# M8 Comparator 自动发布检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-12T00:30:00+08:00`

## 1. 正式 Comparator Version

`workspace.v022_shadow_comparator_version` 保存可运行、可追溯的 Comparator Policy，而不再把一个普通
Artifact UUID 当作算法实现。首个算法 `canonical_projection_equal_v1` 显式声明：

- canonical field key；
- v0.21 Reference Decision 中的读取路径；
- v0.22 Product Decision 中的读取路径。

因此两个版本字段名可以不同，但映射必须由研究者预先冻结。Comparator 不猜字段、不做隐式数值转换，
缺字段与值不同都返回 `different`。

## 2. v0.21 Reference Decision

`workspace.v022_shadow_v021_reference_decision` 保存用于比较的实际 v0.21 决策正文，并绑定 exact：

- Shadow Runtime Binding；
- Representative；
- Decision Session；
- known-at 与不可变 reference fingerprint。

Reference 必须在 Session cutoff 后产生。v0.21 Work Item 只能以属于同一个 Binding 和 Session 的正式
Reference Artifact 完成，不能再提交任意 published Artifact 充当旧系统结果。

## 3. 自动汇合与 fail-closed 差异

`ShadowComparisonCoordinator` 扫描两腿均完成的 Dual-run Intent，读取 frozen Comparator Policy、正式
v0.21 Reference 与 exact v0.22 Product Decision，然后自动调用原有 Comparison publication contract。

- 完全相等：发布 `matched`；
- 字段不同或缺失：发布没有 explanation code 的 `different`；
- v0.22 Decision 为 `missing`：无论投影结果如何都强制 `different`；
- 同一 Intent 使用 PostgreSQL advisory lock，重复运行不会重复发布；
- `maintenance_read_only`、`hidden` 下禁止发布新 Comparison。

自动发现差异不等于解释差异。只有未来新建、明确编码合法差异规则的 Comparator Version，才能产生可
解释的语义；当前实现不会把普通 mismatch 自动洗成“已解释”，Coverage 因而继续 fail closed。

## 4. 验证

- Python unit：411 passed；v0.22 PostgreSQL integration：22 passed；
- projection comparator 覆盖跨版本路径映射、值差异、缺字段、重复 canonical key；
- PostgreSQL 纵向链覆盖 matched 与 missing-v0.22 强制 different；
- Runtime Binding 拒绝非正式 Comparator；Work completion 拒绝非 exact Reference；
- revision 71 迁移、downgrade/upgrade、Ruff 与 strict mypy 通过。

后续 M8 进入 SLO/告警、DB + Object Store restore drill、rollback drill、API mutation guard 与最终
cutover Gate。
