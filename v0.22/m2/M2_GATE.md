# 候鸟 v0.22 M2 Gate

> Contract：`bird-migration-v0.22.0`
> Milestone：`M2 — Graph Core + 最小 deterministic runtime`
> 状态：通过
> 完成时间：`2026-08-10T18:53:14+08:00`

## 1. 数据库切片

M2 按冻结顺序新增四个 additive migration：

1. `20260810_51_v022_workspace`：不可变 Draft Intent/Event/Preview、Command Result、
   Compile Attempt、Compiled Graph、Feature Occurrence、Node/Edge、conditional
   Aggregation Instance 和 Strategy Branch；
2. `20260810_52_v022_graph_dag`：Graph Run、可共享 Work Item、dependency、consumer、
   原子 ready gate、失败传播、lease generation 和 fencing token；
3. `20260810_53_v022_processing`：可复用 Node Run、Manifest input/output、Graph Run
   binding、partition、checkpoint、cache 和逐层导出请求；
4. `20260810_54_v022_deterministic`：deterministic Aggregation Run、Manifest
   input/output、cache 和 Graph Run binding。Strategy Target、Defense Run、最终袖套合并
   只完成 expand/validate，数据库约束保持 `runtime_enabled=false`，等待 M5 解锁。

所有重建、降级和增量升级仅在名称受保护的 `style_rotation_test` 执行；正式数据库写入数为 0。

## 2. 编译契约

- Draft Intent 是严格、可 fingerprint 的逻辑输入；Compile Attempt 与可复用 Compiled Graph
  分离，失败编译同样保存 rejected Attempt 和诊断；
- Solver 只沿 Catalog 中人工声明的唯一 Producer/固定 input binding 展开，不生成随机线路；
- Raw 或较早 Stage 的 Feature 只能逐层 projection，不允许跳层；
- 聚合输入是有序、显式选择的 Stage 3 occurrence；原始数据即使投影至 Stage 3，若 Payload
  Contract 不被聚合器接受仍会拒绝；
- deterministic Aggregation 只展开 Parameter Preset，不展开 Target/Training 轴；supervised
  轴只在 Catalog 明确声明且用户明确选择时形成笛卡尔积；
- `data_availability_revision` 不进入配置 fingerprint；解析后的 Data Binding fingerprint 进入；
- 同一语义 Intent 的集合型选择采用稳定排序，有序聚合输入继续保留用户顺序。

## 3. DAG 与运行契约

- Scheduler 在一个事务内写入全部 Work Item、consumer 和 dependency，检测 work count、
  dangling required edge 和 cycle 后才允许 `planning → ready`；
- Worker 只领取 ready/running Graph Run 中、所有 required upstream 已完成的任务；
- 并发 claim 使用 `FOR UPDATE SKIP LOCKED`，每次领取递增 lease generation/fencing token；
- 完成发布必须匹配 Worker、未过期 lease 和 fencing token；伪造 token 被数据库拒绝；
- required upstream 失败/取消后，下游原子进入 `blocked_upstream_failed` 或
  `blocked_upstream_cancelled`，Graph Run 同步进入终态；
- 共享 Work Item 只有在最后一个未释放 consumer 离开后才允许取消；
- execution fingerprint 覆盖组件版本、参数、有序端口与 Manifest/hash、资源绑定、范围、
  executor/environment、determinism/cache、reader contract 和可选 target/fold identity。

## 4. 最小执行能力

- `single_signal_identity`：严格要求一个输入并保持 missing；
- `flat_equal_weight_mean`：显式输入顺序、complete-case missing policy、Decimal Q18 与
  half-even rounding；
- Processing/Aggregation Run 均以 execution fingerprint 寻址，Graph Run 只保存 executed/reused
  binding，不复制 Run；
- M1 Catalog 仍显式包含 0 个 Processing Node，因此完整可运行代表链按冻结计划在 M3 发布；
  M2 不制造伪节点，也不提前开放 Workspace 前端。

## 5. 验证结果

- 全量 pytest：379 passed，1 个上游 Starlette TestClient deprecation warning；
- Ruff：通过；
- mypy：201 个 source files 通过；
- M2 专项：5 个 pure compiler/runtime unit、5 个 PostgreSQL integration 全部通过；
- 三 Work DAG 双 Worker 并发只领取一次，并按 `node → node → aggregation` 解锁；
- stale fencing、失败传播、Raw Contract 拒绝审计、M1 → M2 additive upgrade 全部通过；
- M0 Oracle 继续 byte-equivalent，payload hash 保持
  `5949ca9b71b68bbde935402d792e89bef58c8d28143fe1f4e7446d78c34c8329`；
- 两个无关 v0.1 用户文档未修改、未暂存。

因此 `m3_entry_allowed=true`。M3 才发布三条代表加工链并建立第一个前端可见垂直切片；
当前 Workspace/default route、Strategy/Defense runtime、LightGBM 和 RV20 继续 hidden/disabled。
