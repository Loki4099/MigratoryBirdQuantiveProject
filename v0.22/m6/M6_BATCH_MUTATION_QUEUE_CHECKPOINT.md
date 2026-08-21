# M6 原子 Batch Event 与 Mutation Queue 检查点

日期：2026-08-11
状态：`passed`（M6 子检查点；M6 Gate 仍开放）

## 后端原子事件

Graph Draft event contract 新增：

- `batch_select_feature_occurrences`
- `batch_deselect_feature_occurrences`

Batch 最多包含 500 个不重复 occurrence。服务端在单一数据库事务中验证全部 occurrence、锁、
Aggregation 兼容性与资源准入；任一成员非法时整批不写 revision、不写 event。成功时无论包含多少
occurrence，都只产生一个 immutable revision 和一条 event history。

Batch deselect 只有在批次同时覆盖全部锁定它的下游显式 occurrence 时才能直接通过；否则仍返回
`cascade_confirmation_required`，不会绕过既有血缘保护。

## 前端 Mutation Queue

Graph Provider 不再为每次点击直接读取渲染时的旧 revision。普通 Feature、Aggregation 和 Frequency
命令进入 FIFO 队列：

```text
command 1 @ revision N → server revision N+1
command 2 @ revision N+1 → server revision N+2
```

- 行级 `aria-busy` 与“等待服务器确认”文本标注 pending occurrence；
- Header 显示 pending command 数；
- Family 可用多个 Variant 时提供原子批量选择；
- clone、rebase、代表链载入和 compile 与普通队列互斥；
- cascade/rebase preview 期间暂停普通队列。

## 冲突恢复

收到 `409 draft_revision_conflict` 后：

1. 当前失败命令出队；
2. 后续命令保持原顺序并暂停；
3. UI 显示“队列已暂停”和“重新加载并继续队列”；
4. 重新读取服务器 Draft 后，剩余命令从最新 revision 继续。

客户端不推测冲突命令是否应重放，避免把用户已在其他标签页完成的操作重复应用。

## 验证

- Python unit：383 passed；
- PostgreSQL Graph Draft integration：4 passed，包括非法 batch 全回滚、成功 batch 单 revision、
  单 event 与幂等 replay；
- Frontend：28 tests passed，覆盖快速连续命令 revision 串行、409 暂停和 reload 后恢复；
- TypeScript、ESLint、production build、Ruff、mypy、OpenAPI committed contract 全部通过。

Vite 主 chunk 超过 500 kB 的警告仍留给后续 M6 性能/拆包检查点。
