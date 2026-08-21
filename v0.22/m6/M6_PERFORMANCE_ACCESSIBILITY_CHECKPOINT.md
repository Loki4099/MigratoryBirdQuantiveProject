# M6 大 Catalog 性能与无障碍检查点

日期：2026-08-11
状态：`passed`（M6 子检查点）

## 大 Catalog 边界

Workspace 不把完整 Family Catalog 一次性塞入首屏。Stage API 使用稳定 view token、服务端搜索、
availability filter 和 cursor pagination；前端每页请求 12 个 Family，并把 selected/required pinned
区域与分页目录分离。搜索输入 250 ms debounce，翻页只追加当前一致视图的数据，revision 或筛选变化会
由 query identity 隔离，避免旧页混入新派生状态。

## 路由拆包

v0.22 Graph Workspace 和 Product 页面改为 route-level dynamic import。Production build 结果：

| Chunk | Minified | Gzip |
|---|---:|---:|
| main | 364.74 kB | 112.69 kB |
| Graph Workspace | 30.38 kB | 9.46 kB |
| Product | 34.70 kB | 10.46 kB |

此前 530.88 kB 的单一主包和 Vite 500 kB 警告已消失。路由 fallback 使用 `role="status"`，按需加载
不会留下无反馈空白页。

## 键盘与辅助技术

- Stage 导航和 Variant 选择均为原生 button，并通过 `aria-pressed` 暴露选择状态；
- mutation pending 同时使用文字、`aria-busy` 和 live output，不只使用颜色；
- lineage / cascade / Catalog rebase 均使用 `role="dialog"`、`aria-modal="true"` 和可访问名称；
- 打开对话框后焦点进入第一个可操作控件，Tab/Shift+Tab 保持在弹窗内；
- Escape 可安全关闭非 busy 对话框，关闭后焦点返回原触发控件；
- 全局 `focus-visible` 样式、skip link 和错误 `role="alert"` 继续保留。

## 验证

- Frontend：30 tests passed；
- TypeScript、ESLint、production build 通过；
- production build 无 oversized chunk warning；
- 键盘测试验证对话框初始焦点、Escape 关闭与焦点恢复；
- 全 v0.22 PostgreSQL integration：19 passed，确认性能拆包未改变 M1–M5 后端契约。
