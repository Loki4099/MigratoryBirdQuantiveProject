# 候鸟 v0.22 M7 Gate

> Contract：`bird-migration-v0.22.0`
> Milestone：`M7 — Experiment/Product 身份与连续运行`
> 状态：通过
> 完成时间：`2026-08-11T17:00:00+08:00`

## 1. Gate 结论

M7 冻结范围已经全部通过：

- 精确 Compiled Branch 发布不可变 Research Configuration，并冻结有序 direct inputs；
- Common Evaluation Panel 与 Result Evidence 保存精确成员、runtime dependencies 和质量身份；
- Comparison 与 matched baseline 按受保护上下文和固定 Treatment Dimensions 分类；
- Product Definition 稳定，Execution、Qualification、Monitoring Policy 独立版本化；
- Enrollment 冻结 OOS anchor，Product Decision 不可跨 Execution 拼接且允许显式 missing；
- Lifecycle 由 append-only events 推导，OOS health 由 exact prospective membership 版本化发布；
- Experiment/Product 只读 API 与第一屏显示最终 Aggregator、直接信号、Strategy 和 Defense。

## 2. Fail-closed 边界

- Configuration、Evidence、Comparison、Product 版本和监控快照一经发布不可原位修改；
- deterministic Aggregator 不展开 Target/Training Preset 轴，训练型 Aggregator 必须冻结对应身份；
- direct inputs 保持编译顺序，不能从上游全图或 latest Catalog 重新推断；
- Comparison 缺少合法基线时发布显式 `missing`，不能回退到语义不匹配结果；
- Product Decision 缺失时保存 missing 身份，不使用其他 Execution 或未来已知数据补齐；
- 未来生效的生命周期事件不会进入当前状态，OOS 监控只纳入锚点后的 eligible sessions；
- 第一屏 API 失败只降级只读身份面板，不改变既有 Experiment/Product 行为。

## 3. 验证结论

- Python unit：398 passed；
- Frontend：31 passed；TypeScript、ESLint、production build 通过；
- 全 v0.22 PostgreSQL integration：20 passed；
- M7 定向 database foundation + Graph Draft/Identity API integration：9 passed；
- Ruff、strict mypy、OpenAPI committed contract 和生产构建通过；
- 两个无关 v0.1 用户文档未修改、未暂存。

因此 `m8_entry_allowed=true`。下一阶段按冻结计划进入 M8：兼容、shadow、cutover、回滚演练与最终用户
验收。M7 Gate 不授权提前切换默认入口；完成全部 v0.22 开发后再启动前端 UI 供用户实际操作。
