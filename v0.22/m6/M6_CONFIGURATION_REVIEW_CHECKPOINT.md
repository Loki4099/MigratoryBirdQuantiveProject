# M6 Aggregation、Strategy、Defense 配置与 Review 检查点

日期：2026-08-11
状态：`passed`（M6 子检查点；M6 Gate 仍开放）

## 交付范围

Workspace 已将研究分支的三个配置轴接入同一份不可变 Draft Intent：

- Aggregation Family 及显式 parameter preset；
- Cross-Section Strategy Variant；
- 可选 Defense Package；不选择 Defense 表示 `none`，不是一条隐式 Catalog 记录。

每次选择或取消选择均通过带 `expected_revision` 与 idempotency key 的 Draft Event 写入，继续复用
FIFO mutation queue、跨标签 revision 同步和 409 fail-closed 机制。Review 不维护第二份前端状态，只显示服务端
Derived View 的最终信号、Aggregation instance、Strategy、Defense、branch/cell 和资源准入结果。

## 显式配置边界

即使 Aggregation Family 当前只有一个可用 preset，也必须由用户显式选中。系统不会因为选项唯一而自动补齐；
这保证 Draft Intent、编译图和后续 Experiment configuration identity 完全一致。取消 Aggregation Family 时，
其 preset 选择一并移除，避免不可见的陈旧参数影响后续指纹。

Strategy 和 Defense 都使用稳定 variant key。Strategy 声明支持的调仓频率；当前 Workspace 频率不兼容时，
选项显示原因并产生 `frequency_unsupported` blocker，不能进入编译。未知或已退出 Catalog Release 的 key
同样由统一派生校验拒绝，不做名称猜测或自动替换。

## 分支语义

用户可同时选择多个 Aggregation、preset、Strategy 和 Defense 进行比较，但每个编译后的实验分支只有：

```text
one Aggregation instance
+ one Strategy variant
+ zero or one Defense package
```

选择多个选项只展开独立分支，不会把多个模型或多个防御策略混入同一实验。编译后的 Aggregation instance
保存不可变 preset version ID；测试通过 Catalog identity 表回查 key，不在编译表重复保存可变文本身份。

## 前端

- Aggregation 页展示并编辑各 Family 的显式 preset；
- Strategy / Defense 页展示名称、family/variant key、参数、频率或资产上下文兼容性；
- Review 页统一列出最终 Stage 3 输入、Aggregation + preset、Strategy、Defense 和精确资源规模；
- 已选择项继续在选项区前置并保留文字状态，不只依赖颜色表达。

## 验证

- Python unit：385 passed（包含唯一 preset 不得隐式选择、Strategy 频率不兼容边界）；
- Frontend：30 tests passed，包含 preset 与 Defense 事件的连续 revision 顺序；
- PostgreSQL Graph Draft integration：5 passed，验证 3 个 Aggregation instance、6 个 Strategy branch、
  39 个 backtest cell 及编译后 preset version identity；
- Ruff、mypy、TypeScript、ESLint 和 OpenAPI 生成检查通过。

M6 Gate 仍未关闭：下一检查点继续处理大 Catalog 渲染性能、路由拆包、无障碍和最终 production gate。
