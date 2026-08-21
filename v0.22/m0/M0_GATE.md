# 候鸟 v0.22 M0 Gate

> Contract：`bird-migration-v0.22.0`
> 状态：M0 evidence generated；最终 Gate 由 `m0-evidence-manifest.v0.22.0.json` 与自动验证决定
> 基线时间：2026-08-10

## 1. 已冻结身份

- v0.21 source commit：`85a600811b2f58a7bb4be13b2a9c707035891d98`
- v0.22 contract commit：由 annotated tag `v0.22.0-contract` 解析
- Schema revision：`20260809_47_signal_export_job`
- Python：3.12
- PostgreSQL：16
- 依赖与 Catalog：逐文件保存 canonical-LF SHA-256，并验证与 v0.21 source commit 一致

## 2. v0.21 Oracle

不可变 Oracle 位于 `v021-baseline-manifest.v0.22.0.json`。它冻结现有 Artifact ID、semantic fingerprint 与 content hash；parity 运行不能调用 v0.21 calculator 生成期望结果。

当前库存：

| 对象 | 逻辑版本 | 冻结数据集 |
|---|---:|---:|
| Factor | 12 Family / 28 Variant | 56 |
| Signal | 27 Family / 51 Version | 102 |
| Model | 86 Specification | 172 |

另外冻结：

- 全部现有 Engine Version；
- 10 个 Workspace Compiled Model Instance；
- 14 个 Compiled Strategy Version；
- 26 个已接受 Cell Result；
- 1 个 active Product 及其 218 个上游 Artifact；
- active Product 使用的 `linear_weighted__signal_equal_v1`，作为 v0.22 `flat_equal_weight_mean` 的 canonical Oracle。

## 3. 资源、性能与 Feature Gates

`m0-policy.v0.22.0.json` 冻结：

- Workspace Derived View、Preview、Compile、Lineage 的 P50/P95/timeout；
- Graph、Branch、Cell、Fold、Export、CPU、内存和并发硬上限；
- M1 开始时只有 Contract 与 M0 Oracle Gate 开启；
- v0.22 Workspace、Runtime、默认路由、LightGBM 与 RV20 均保持 hidden/disabled；
- 正式数据库只允许 read-only inventory/oracle verification；破坏性测试只能进入 `style_rotation_test`。

这些是首版受信任单用户环境的 admission defaults。修改研究语义必须新增 Contract Version；仅调整运行预算也必须提供 benchmark evidence 和 Operational ADR。

## 4. M0 完成条件

只有以下条件全部为真，才能进入 M1：

1. Frozen Contract 与 Git tag 校验通过；
2. v0.21 Baseline Manifest 可在 repeatable-read 只读快照下重新生成且 byte-equivalent；
3. 28/51/86 库存与 56/102/172 输出数据集数量精确一致；
4. 所有引用 Artifact 均为 `published` 且未 invalidated；
5. active Product 血缘闭包非空，signal-equal Oracle 已冻结；
6. M0 policy JSON 可解析，所有非 M0 Feature Gate 仍关闭；
7. unit、ruff、mypy 和选定的 PostgreSQL integration tests 通过；
8. 两个用户 v0.1 文档未被暂存、提交或改写。

M0 Evidence Manifest 发布后不可覆盖。若后续发现 Oracle 错误，必须发布新的 Baseline Version，并保留本版本作为失效证据。

## 5. M0 最终验证结果

M0 于 `2026-08-10T16:58:11+08:00` 通过。机器可读证据位于
`m0-evidence-manifest.v0.22.0.json`，结论如下：

- v0.21 Oracle 在 repeatable-read 只读事务中重建并通过 byte-equivalent 校验；
- 320 个 unit tests 全部通过；
- Ruff 全量检查通过；
- mypy 检查 193 个 source files，无错误；
- 5 个 Factor/Signal/Model/Strategy/Product 核心 PostgreSQL 集成测试通过；
- 破坏性数据库操作受 `style_rotation_test` 名称保护，正式库写入数为 0；
- 两个无关的 v0.1 用户文档继续保持未跟踪，未纳入 M0。

因此 `m1_entry_allowed=true`，可以开始 M1；所有 M1 之后的功能开关仍保持关闭。
