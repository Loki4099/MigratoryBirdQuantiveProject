# M6 多标签页 Revision 同步检查点

日期：2026-08-11
状态：`passed`（M6 子检查点；M6 Gate 仍开放）

## 协议

每个 Workspace Graph Provider 建立同源 `BroadcastChannel`，并持有本标签页随机 `sourceId`。
只有服务器确认并写入 Query Cache 的 Graph Draft Snapshot 才广播：

```json
{
  "sourceId": "tab-local-id",
  "graphDraftId": "uuid",
  "revision": 42
}
```

不广播本地 optimistic state，因为本实现不存在权威 Intent 的本地副本。

## 接收规则

接收方只处理同时满足以下条件的 notice：

- 来源不是当前标签页；
- `graphDraftId` 与当前 Draft 一致；
- notice revision 严格大于当前服务器 Snapshot revision。

符合条件时自动重新读取服务器 Draft。旧 notice、重复 notice、其他 Draft notice 和自身 notice 均忽略。

## 与 Mutation Queue 的关系

若收到更高 revision 时本标签有执行中或待执行命令：

- 队列进入 paused；
- 立即刷新服务器 Snapshot；
- 保留尚未发送命令的顺序；
- UI 明确说明另一个标签页已推进 revision；
- 用户确认“重新加载并继续队列”后，才从最新 revision 恢复。

已在服务器执行中的命令仍由后端 optimistic revision CAS 决定成功或返回 409，客户端不伪造取消。

## 生命周期

Provider unmount 时关闭 channel；不支持 BroadcastChannel 的环境保持单标签行为，不影响 Draft API。
Local Storage 仍只用于恢复最近 Draft 身份，不保存权威 Draft Intent。

## 验证

- Frontend：29 tests passed；
- 双独立 QueryClient/Provider 测试验证标签页 A 的 revision 2 会触发标签页 B 自动读取并显示 revision 2；
- 既有快速队列串行与 409 暂停/恢复测试继续通过；
- TypeScript、ESLint 和 production build 通过。

Vite 主 chunk 超过 500 kB 的警告继续留给后续 M6 性能/拆包检查点。
