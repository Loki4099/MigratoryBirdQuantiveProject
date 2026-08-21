# M6 Workspace 资源预估与准入检查点

日期：2026-08-11
状态：`passed`（M6 子检查点；M6 Gate 仍开放）

## 交付范围

Workspace Derived View 现在返回基于冻结 M0 policy 的结构资源报告，覆盖：

- Stage 3 显式输入；
- Feature occurrence 与自动祖先 occurrence；
- Node input edge 与 layer projection edge；
- Aggregation candidate 与实际 instance；
- Strategy/Defense candidate 与实际 branch；
- 预计 backtest cell 与 work item。

每个维度同时返回估计值、冻结上限和 `accepted/rejected`。任一维度超限会产生
`admission` blocker，精确编译继续复用统一 blocker 机制 fail-closed。

## 身份修正

Derived State/Selection fingerprint 现在包含 Aggregation parameter preset、Strategy 和 Defense
选择。改变实验分支轴不会再错误复用相同派生身份。

实例与分支估计遵循编译器同一笛卡尔积语义：

```text
aggregation instances
  × selected strategies
  × selected defenses
= strategy branches

backtest cells = aggregation instances + strategy branches × 6
```

确定性 Aggregation 不展开 Target/Training 轴；多 preset Family 未显式选择 preset 时仍保持 blocker，
不以推荐值充当隐式默认。

## 前端

Review 页面显示：

- branch / cell 数；
- admission 状态与 policy ID；
- occurrence、edge 和 work-item 摘要。

资源状态不只通过颜色表达，同时提供文本状态、数字和 blocker reason code。

## 验证

- Python unit：383 passed；
- Frontend：26 tests passed，TypeScript、ESLint 和 production build 通过；
- PostgreSQL Graph Draft integration 验证预估 occurrence、edge、instance、branch 与实际编译图一致；
- Ruff、mypy、OpenAPI committed-contract 检查通过。

Vite 仍保留主 chunk 超过 500 kB 的已知警告，继续归入 M6 大列表/拆包性能检查点。
