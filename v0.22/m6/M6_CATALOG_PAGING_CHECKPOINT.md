# M6 Catalog 一致分页检查点

日期：2026-08-11  
状态：`passed`（M6 子检查点，M6 Gate 仍开放）

## 交付范围

新增 Graph Draft Stage Family 查询：

`GET /api/v2/workspace/graph-drafts/{draft_id}/stages/{stage_no}/families`

接口支持名称、key、经济含义和 payload contract 搜索，支持选择状态与合法性过滤，并以 opaque
cursor 分页。Pinned Family 在独立区域完整返回，不受搜索和过滤影响；Catalog page 不复制 Pinned
Family。

Draft GET、创建和事件响应只保留 Stage 状态计数，不再返回完整候选 Family/Variant。前端候选目录
因此必须经过分页接口加载，避免大 Catalog 在初次恢复 Draft 时整包传输。

## 一致性契约

每一页返回统一 `view_token`，其内容指纹绑定：

- Draft revision；
- Catalog Release ID；
- resolved Data Binding fingerprint，作为当前冻结数据可用性 revision；
- Derived State fingerprint。

cursor 进一步绑定 stage、标准化搜索词、selection filter、availability filter 和 page limit。
不同查询不能复用 cursor；Draft revision 或合法性快照变化后，旧 cursor 返回
`409 workspace_view_token_conflict`，前端重新读取 Draft，绝不拼接新旧页面。

## 前端行为

- 250ms debounce 后进行服务端搜索；
- Pinned 区始终位于 Catalog 搜索结果之前；
- ready/requires ancestors/hard incompatible 服务端过滤；
- cursor “加载更多”而不是客户端截断整包 Catalog；
- revision 进入 React Query key，mutation 返回新 revision 后自动建立新分页视图；
- 搜索、过滤、结果计数和错误重试具有键盘 label、focus-visible 与 `aria-live` 状态。

## 验证

- Python unit suite：380 passed；
- Frontend：TypeScript、ESLint、25 tests、production build 全部通过；
- PostgreSQL integration：Pinned 不被无结果搜索隐藏；同 token 可继续翻页；查询条件漂移 fail-closed；
  mutation 后旧 cursor 返回 409；
- OpenAPI JSON 与生成的 TypeScript schema 已同步。

Vite 仍报告主 chunk 大于 500 kB；路由级 code splitting 与大列表渲染性能属于后续 M6 检查点，
不在本检查点中伪装为已完成。
