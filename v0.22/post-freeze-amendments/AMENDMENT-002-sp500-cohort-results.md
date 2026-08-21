# AMENDMENT-002：历史标普、统一实验环境与结果工作流

> 状态：accepted
>
> 批准日期：2026-08-16
>
> 影响范围：v0.22 数据发布、Evaluation Cohort、Graph Suite admission、实验结果与 Product 候选工作流

用户已批准 [`v0.22 历史标普数据、统一实验环境与实验结果前端更新计划`](../v0.22历史标普数据_统一实验环境与实验结果前端更新计划.md)。该计划冻结以下开发边界：

1. 本轮只建设按历史生效日变化的 S&P 500 Universe；
2. 将本机已有历史成员、身份修复、公司行动和 2013–2026 行情作为受控导入种子，补齐 2004-12-31 至 2012 年末后，在 v0.22 内重新发布不可变身份与血缘；
3. 使用 504 个完整 XNYS session 暖机；覆盖审计候选为 `warmup_start=2004-12-31`、`evaluation_start=2007-01-03`、`evaluation_end=2026-06-30`；
4. 同一 Evaluation Cohort 的所有配置共享完全相同的 Universe、Eligibility Mask、数据、起止日、Benchmark、成本、日历和引擎版本；覆盖不足必须拒绝，禁止动态移动起点；
5. 周频与月频形成独立 Cohort 和排行榜；每个 Portfolio Cell 单独占一行并可独立升级 Product 候选；
6. 有证据且全策略统一排除的 provider-unavailable Security 可进入带明确警告的 `rankable_research`；排行榜和 Product 准入的后续分离规则由 [`AMENDMENT-003`](AMENDMENT-003-free-data-research-product.md) 覆盖。

本修订记录已批准的工程方向，但不以文本直接改写 `bird-migration-v0.22.0` 的冻结字节或既有数据库身份。实施新增的 Schema、Artifact、状态机、API 和运行语义时，必须通过显式 Contract Version、append-only migration 和对应测试发布；旧 Dataset、Suite、Result 与 Product 身份保持可读且不得伪造回填。
