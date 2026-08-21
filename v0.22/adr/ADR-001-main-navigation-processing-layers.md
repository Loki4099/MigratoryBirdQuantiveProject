# ADR-001：把 v0.22 Graph Draft 并回候鸟主研究选择链

> 状态：accepted
>
> 确认时间：2026-08-12
>
> 决策来源：用户在前端可见验收中明确否定独立“三层加工工作台”
>
> 影响范围：前端信息架构、路由、发布状态切换和前端测试
> 不改变：`bird-migration-v0.22.0` 的 Graph、Catalog、血缘、编译与运行语义

## 背景

M3/M6 为了并行开发和保护 v0.21 默认入口，先增加了独立 `/workspace-v022` Graph Workspace，
并把 Raw、三个加工层、聚合、策略和 Review 放在同一页面的内部 Tab。实际验收确认这只是迁移期
垂直切片，不应成为最终产品信息架构。

最终候鸟仍采用原有的逐层主导航和页面心智模型，但原来的“因子 → 信号 → 模型”必须整体替换为：

```text
资产 / 原始输入
→ 加工层 1
→ 加工层 2
→ 加工层 3
→ 聚合层
→ 策略与防御
→ 检查并编译
```

其中 Raw Input 内部仍是 `stage_no=0`，不计入三个加工层。Stage 3 是最终信号选择层，不是模型层；
模型类加工允许出现在任意加工层，原 v0.21 Model 的聚合职责迁移到 Aggregation。

## 决策

1. 删除最终侧栏中的独立 `Workspace v0.22` 入口和单页内部 Stage Tab。
2. v0.22 主导航直接显示资产/原始输入、加工层 1/2/3、聚合层、策略与防御、检查并编译。
3. 四个加工/聚合页面以及策略、Review 必须位于同一个 `GraphDraftProvider + Outlet` 父路由下，
   跨页不能卸载 Provider；revision、FIFO mutation queue、pending impact、BroadcastChannel 和 compile
   结果必须连续。
4. v0.22 页面只使用 persistent Graph Draft 作为选择权威。不得把 v0.21 的
   `factorVariantKeys/signalVersionKeys/modelPresetKeys` 改名冒充三个加工层和聚合层。
5. v0.21 `WorkspaceSelectionProvider` 仅包裹 v0.21 回退页面，不与 v0.22 Graph Provider 同时挂载。
6. App 读取 `GET /api/v2/release-control` 决定默认信息架构：

| Release State | 默认导航 | v0.22 编辑 |
|---|---|---|
| `hidden` | v0.21 | 禁止，不创建 Graph Draft |
| `shadow` | v0.21 | 禁止，不创建 Graph Draft |
| `explicit_eligible` | v0.21；用户显式进入 v0.22 | 允许 |
| `default` | v0.22 主加工链 | 允许 |
| `maintenance_read_only` | 专用只读恢复界面 | 禁止新写入 |

7. 迁移期保留 `/workspace-v022/*` 作为显式预览命名空间，但其页面结构与最终主导航相同，
   不再渲染“三层加工工作台”。进入 `default` 后使用无前缀主路由。
8. 旧 `/factors`、`/signals`、`/models` 和旧 Workspace 继续作为 v0.21 回退面存在；默认切换后不在
   主导航显示，不能无条件重定向而破坏 rollback。

## 本切片实现

- 新增 release-control 前端客户端；
- 隔离 v0.21 与 v0.22 Provider；
- 建立跨页持久 Graph 父路由；
- 拆出资产/Raw、加工 1/2/3、聚合、策略/防御、Review 页面；
- 侧栏按 release/default 或显式 v0.22 路由切换；
- hidden/shadow fail-closed，不再先 POST create 再显示 409；
- 旧 `/workspace-v022` 自动进入新的加工层 1 页面结构；
- 增加 stage 路由映射、单 Draft Provider、release-aware 导航和 hidden 不创建 Draft 的测试。

## 切换默认前仍需完成

以下是原计划功能闭环，不得用旧 v0.21 页面或浏览器本地假实现替代：

1. Asset Context/Data Binding 的 Graph Draft 选择或 clone 流程；当前 Graph create 仍使用冻结默认 Context。
2. 以 `compiled_research_graph_id` 创建 v0.22 Suite 的公共 API 与前端按钮。
3. 真正的逐层 Payload 数据导出 job/API；当前 JSON 下载只是血缘清单。
4. 完整 Raw → Stage 3 Node input binding 血缘读取接口；当前 Inspector 只到 producer node/output port。
5. maintenance 下按可信 actor/draft key 只读找回现有 Draft，且所有 mutation 控件禁用。
6. 真实 Shadow Plan、Parity Gate 与后续发布 Evidence；不得为 UI 展示伪造 Gate。

## 验收条件

- 最终导航不出现 Factor/Signal/Model 编辑层或独立“三层加工工作台”；
- `processing-1/2/3` 精确请求 `stage_no=1/2/3`，Raw 保持 `stage_no=0`；
- 页面切换只创建/恢复一次 Draft，revision 和 pending 命令连续；
- Stage 3 显式选择是唯一 Aggregation input 来源；
- selected/required 置顶、非法项置灰、反向祖先点亮、锁和级联确认全部保留；
- release 状态与默认导航、写入准入一致；
- v0.22 页面不保存或提交任何 v0.21 Workspace Draft。
