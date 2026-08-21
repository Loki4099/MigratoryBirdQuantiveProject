# 候鸟 v0.22 M3 Graph Context 与前端 Provider 检查点

日期：2026-08-10

## 本检查点已完成

- Graph Draft 创建命令只接收可读的 Asset Context key 与 Data Input key；浏览器不再构造
  Asset/Data fingerprint。
- 后端从已发布 Asset Registry、固定 Asset Set、Security identity、Dataset Publication、
  Calendar 与 Coverage 解析并冻结完整 Context/Binding 文档及 fingerprint。
- 新增 `20260810_58_v022_graph_context`，旧 Draft 以明确的
  `legacy_fingerprint_only` 文档兼容迁移，不伪造已解析身份。
- 同一 researcher/draft 语义在浏览器丢失 localStorage 或首次响应丢失后可按逻辑身份恢复；
  同逻辑 key 的不同创建语义仍拒绝复用。
- 前端 `GraphDraftProvider` 负责创建/恢复、事件保存、revision conflict 刷新、级联取消预览
  与确认，以及精确 revision Compile。
- Workspace 展示后端保存 revision 和 Asset Context；最终信号选择、聚合器选择、频率变更
  与代表链载入均写入不可变 Draft Event，不再使用一次性 preview 本地状态。
- selected-first、不可随意取消的上游血缘、逐层导航、血缘抽屉、层级导出与 Review 保留。

## 验证结果

- 真实 PostgreSQL Context/Draft/Compile 集成测试：2 passed。
- API/OpenAPI：26 passed；v0.22 单元套件：27 passed。
- Ruff、mypy、TypeScript、ESLint：通过。
- 前端 Vitest：25 passed；production build：通过。
- 浏览器验收：最终信号选择使 revision `1 → 2`，Raw/Processing 1/Processing 2 自动点亮；
  刷新后 Draft 与 selected-first 状态恢复；血缘抽屉显示人工 Node/Port；Review 成功返回
  graph fingerprint；控制台 0 error。
- Vite 仍提示单 chunk 约 518 kB，大 Catalog 分包留在 M6，不阻塞 M3 垂直切片。

最终里程碑结论见 [`M3_GATE.md`](M3_GATE.md)。
