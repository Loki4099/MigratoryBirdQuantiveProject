# 候鸟 v0.22 M1 Gate

> Contract：`bird-migration-v0.22.0`
> Milestone：`M1 — Payload + 全 Catalog Identity`
> 状态：通过
> 完成时间：`2026-08-10T18:10:58+08:00`

## 1. 数据库切片

M1 按冻结顺序新增三个 additive migration：

1. `20260810_48_v022_payload`：共享 Payload Contract/Compatibility、Encoding、Object、
   Manifest、Partition、Quality、Publication Lease 与 retention/materialization 状态；
2. `20260810_49_v022_catalog`：Processing Feature/Node/Port/Binding/Producer，以及
   Aggregation/Target/Preset/Strategy/Defense 全身份；
3. `20260810_50_v022_release`：Catalog Release、精确 membership、Publisher
   Authorization 与 Validation Evidence。

数据库约束已经拒绝非相邻加工边、错误端口归属、未声明的 Payload 兼容、Raw Producer、
重复 Producer 和非 append-only 修改。正式数据库未执行 migration，写入数为 0；M1 的空库和
v0.21 增量升级只在 `style_rotation_test` 验证。

## 2. 最小完整 Catalog Release

Release `bird_v022_catalog / 0.22.0` 包含 67 个精确组件：

| 对象 | 数量 |
|---|---:|
| Payload Contract Family / Version | 5 / 5 |
| Physical Encoding | 1 |
| Raw Feature Family / Variant / Version | 9 / 9 / 9 |
| Processing Node | 0 |
| Deterministic Aggregation Family / Version | 4 / 4 |
| Aggregation Preset Definition / Version | 6 / 6 |
| Strategy Family / Variant / Version | 1 / 1 / 1 |
| Defense Family / Variant / Version | 2 / 2 / 2 |

Processing Node 为 0 是显式边界：M1 只建立可验证的身份、端口和发布基础设施；三条代表链在
M3 发布，不能为了填满三层制造伪节点。

```text
source_manifest_hash = 6d6aa878ce6d227f8eac19406a216fd53c72d044707f10e6ad8e89643295d15c
release_fingerprint  = 461717206df62b0ccd167ed55ea847067d320827f46f02775f2d2b8697d06251
```

同一 Release 第二次发布复用 67/67 个组件、Release 和 Evidence；数据库重建得到相同
fingerprint 与有序 membership。

## 3. Contract 与发布边界

- Pydantic models 全部 `extra=forbid`；
- v0.22.0 固定三个 Processing Stage 和四个确定性 Aggregation Family；
- deterministic Family 禁止 Target/Training 轴；
- 首版 Node input 只允许 `required` 固定 binding；
- Release 文件路径必须位于 Catalog root 内；
- Publisher/Reviewer 来自本地认证配置，与 Release 声明不一致时在事务开始前拒绝；
- `lint/diff/plan` 纯读，`publish/verify` 产生或重建不可变 Artifact。

## 4. 验证结果

- 全量 pytest：369 passed，1 个上游 Starlette TestClient deprecation warning；
- Ruff：通过；
- mypy：198 个 source files 通过；
- M0 Oracle：byte-equivalent，payload hash 保持
  `5949ca9b71b68bbde935402d792e89bef58c8d28143fe1f4e7446d78c34c8329`；
- v0.21 Factor/Signal/Model/Strategy/Product 发布路径保持通过；
- 两个无关 v0.1 用户文档未修改、未暂存。

因此 `m2_entry_allowed=true`。Workspace、Processing Runtime、默认路由、LightGBM 和 RV20
Feature Gate 继续保持 hidden/disabled；M1 没有提前开放用户功能。
