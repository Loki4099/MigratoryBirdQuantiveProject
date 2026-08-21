# M7 Experiment/Product 身份 API 与第一屏检查点

> Contract：`bird-migration-v0.22.0`
> 状态：完成
> 完成时间：`2026-08-11T17:00:00+08:00`

## 1. 只读 API

新增四个 v0.22 专用只读端点：

- `GET /api/v2/v022/experiments`：列出已发布 Result Evidence 及冻结 Configuration；
- `GET /api/v2/v022/experiments/{evidence_id}`：补充证据质量、Comparison 和 matched baseline；
- `GET /api/v2/v022/products`：列出 Enrollment、Execution、Configuration、Lifecycle、Health 与 OOS 锚点；
- `GET /api/v2/v022/products/{enrollment_id}`：补充 Qualification、Monitoring Policy、生命周期事件、
  监控快照和最近决策。

查询始终从不可变 Configuration Snapshot、Product Execution Version 和 append-only runtime 表读取，
不会从 latest Catalog 重新拼装历史身份。当前生命周期只采用 `effective_at <= now()` 的事件；最近监控按
`known_at, created_at` 倒序确定，避免未来事件或并列时间造成漂移。

## 2. 第一屏

Experiment 与 Products 旧页面顶部新增只读的 v0.22 冻结身份面板，旧筛选、详情、晋升和运行流程不变。
面板明确显示：

- 最终 Aggregator Family、执行模式，以及存在时的 Target/Training Preset 身份；
- 按冻结顺序进入聚合的 direct signals，不展示更上游加工层；
- 横截面 Strategy 与可显式为 `none` 的 Defense；
- Experiment 的 Evidence Class、Comparison 和 matched baseline 数量；
- Product 的 Execution Version、Lifecycle、Health、OOS anchor、监控快照和最近决策状态。

多身份通过选择器切换；未发布身份显示明确空态；v0.22 API 暂时不可用时仅降级该面板，不阻断旧页面。
面板使用生成的 OpenAPI 类型，并为开放 JSON 文档执行运行时类型收窄。

## 3. 验证

- OpenAPI committed contract 与 TypeScript client 已重新生成；
- PostgreSQL 集成覆盖四个端点及 deterministic Aggregator、有序 direct input、Product `watch`
  health、显式 missing decision 和两期监控快照；
- Frontend TypeScript、ESLint、31 tests 和 production build 通过；其中目录到详情测试固定使用
  Result Evidence Snapshot ID，而不是同一记录的 Artifact ID；
- 窄屏下 Configuration 与事实区域降为单列，选择器保持键盘可操作。
