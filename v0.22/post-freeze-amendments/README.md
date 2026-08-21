# 候鸟 v0.22 冻结后修订记录

本目录只登记 `bird-migration-v0.22.0` 冻结后的实现进度、产品信息架构决策和后续契约候选项。
它不修改、解释或覆盖冻结契约；任何 Graph、Catalog、编译或运行语义变化仍须新 Contract Version。

## 当前工程状态

- M0—M8 工程实现与运维闭环已完成；
- 可信 actor、operator-only 运维、backup/restore、Object Store 强根回读、自动 rollback probe、
  只读 release preflight 与显式 transition CLI 已落地；
- 真实生产 Gate Evidence 尚未授权进入 `default`，当前不得为 UI 展示伪造切换；
- M8 操作顺序见 [`../m8/M8_RELEASE_RUNBOOK.md`](../m8/M8_RELEASE_RUNBOOK.md)。

## 已接受实现决策

1. [`AMENDMENT-001：恢复冻结规范索引完整性`](AMENDMENT-001-normative-index-integrity.md)
   - 恢复 Freeze Manifest 冻结的 README 精确字节与 SHA-256；
   - 将工程进度和后续 ADR 迁到本未冻结索引。
2. [`ADR-001：把 v0.22 Graph Draft 并回候鸟主研究选择链`](../adr/ADR-001-main-navigation-processing-layers.md)
   - 最终前端不保留独立“三层加工工作台”；
   - 原 Factor / Signal / Model 主流程替换为加工层 1 / 2 / 3 与唯一聚合层；
   - v0.21 回退界面与 v0.22 Graph Draft 严格隔离；
   - 发布状态失败、维护或不允许对应契约写入时，前端失败关闭。
3. [`ADR-002：检查与编译是策略工作流动作，不是加工层`](../adr/ADR-002-review-compile-is-workflow-action.md)
   - 从主导航移除独立“检查并编译”；
   - 将完整配置检查与显式编译嵌入策略页底部。
4. [`AMENDMENT-002：历史标普、统一实验环境与结果工作流`](AMENDMENT-002-sp500-cohort-results.md)
   - 本轮仅建设历史 S&P 500 Universe，并受控复用、扩充既有本地数据种子；
   - 冻结 504-session warmup、统一评价区间候选、周/月独立 Cohort 和禁止动态移动起点；
   - 冻结一 Cell 一行排行榜、结果详情和逐行 Product 候选升级的开发边界。
5. [`AMENDMENT-003：免费多源数据与带警告的研究 Product`](AMENDMENT-003-free-data-research-product.md)
   - 将严格排行榜准入与研究 Product 准入分离；
   - 允许冻结、可解释、可重放的免费数据质量问题以永久警告方式进入 Product；
   - 冻结 Nasdaq WIKI + SEC/OpenFIGI + 交易所/FINRA + 人工 Evidence 的多源修复路线。
6. [`AMENDMENT-004：双频启动、原生分层聚合与训练型模型集成`](AMENDMENT-004-dual-frequency-and-trainable-aggregation.md)
   - 冻结周/月受控 Launch Batch、严格分榜和先决性能门；
   - 冻结维度间等权、维度内等权的原生分层 Recipe；
   - 冻结 H5/H10/H21 连续横截面 Target、独立模型分支和同模型两级等权集成。
7. [`AMENDMENT-005：双频启动修复、研究轮次重置与结果保留`](AMENDMENT-005-launch-reset-retention-results.md)
   - 修复 Feature Schema 全局复用与单图归属冲突，冻结实例绑定和部分 Batch 恢复；
   - 将 Reset 定义为关闭 Research Round、精确取消双频任务并创建无默认 4 ETF 的空白新 Round；
   - 冻结 Product 强根永久保留、普通实验可达性 GC、实验详情与 Product 回测/OOS 分区展示。
