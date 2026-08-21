# 候鸟 v0.22 M4 独立逐点 Parity 检查点

日期：2026-08-11

## Oracle 边界

- M0 baseline 已重新执行 byte-equivalent 验证，payload SHA-256 仍为
  `5949ca9b71b68bbde935402d792e89bef58c8d28143fe1f4e7446d78c34c8329`。
- 期望值直接读取 v0.21 生产库中 M0 绑定的不可变 Factor/Signal Dataset；读取前在同一
  repeatable-read、read-only 事务中校验 Artifact status、semantic fingerprint、content hash、
  bundle key/version。
- 实际值从对应 bundle 的原始 daily bars 经 v0.22 compatibility runtime 计算，不使用旧
  Factor/Signal 点值生成实际结果。

## 首轮失败与契约修正

首轮结果是 28/28 Factor 通过、51/51 Signal 失败。所有 Signal 都多输出 benchmark SPY，且
共同候选资产的横截面排名受 benchmark 影响。审计 v0.21 发布路径后确认：Factor Dataset
包含 candidate 与 benchmark，而 Signal 只读取 `universe_member.role='candidate'`。

因此发布 Registry `0.22.3`，在每条 Signal recipe 中冻结
`input_asset_role=candidate`。这不是容差放宽或数据点特判；它恢复旧系统统一的资产作用域契约。

## 最终结果

- 28/28 Factor Variant 通过；
- 51/51 Signal Version 通过；
- 两套冻结 bundle 上下文，共 158 个比较全部通过；
- 79 个迁移对象零失败、零 unexplained mismatch；
- Evidence fingerprint：
  `c2b8276e9c7874251b14ff580a003a31a88707b65ed9c1f885b827f3b9abff9f`。

比较分别记录行键缺失/额外、数值 mismatch、Signal state/event mismatch、最大绝对和相对误差。
旧系统没有 missing reason 明细，因此 Evidence 明确标记 `legacy_unknown_not_claimed`，不声称
missing reason 等价。

## 状态边界

当前已形成可确定性重建的独立 Evidence 文件。后续检查点已经增加通用 Migration Registry /
Parity Evidence 数据库发布契约；JSON Registry 仍作为执行源台账保留原状态，数据库中的发布
Registry 才能在 79 份 Artifact 全部成功后原子地标记 `parity_passed`。
