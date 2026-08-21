# 候鸟 v0.22 M4 开发记录

M4 负责 28 个 Factor Variant、51 个具体 Signal Version 的等价迁移与独立 parity Evidence。

当前顺序：

1. 冻结完整 Migration Registry 和 M0 Oracle 绑定；
2. 按人工 Catalog 部署所有兼容节点与逐层 Projection；
3. 执行增量/分片复用与历史修订失效测试；
4. 对每个具体 Variant/Version 发布独立 parity Evidence；
5. 28/28、51/51 全部通过后生成 `M4_GATE.md`。

当前状态：M4 Gate 已通过。增量规划见 `M4_INCREMENTAL_PLANNING_CHECKPOINT.md`，单输出真实
Payload 复用见 `M4_PAYLOAD_REUSE_CHECKPOINT.md`，多输出联合发布与故障回滚见
`M4_ATOMIC_OUTPUT_CHECKPOINT.md`，最终结论见 `M4_GATE.md`。下一阶段进入 M5。

Registry 中的 `mapped` 只表示身份和旧 recipe 已建立映射，不表示节点已部署或 parity 已通过；
`parity_passed` 必须绑定独立 Evidence Artifact，blocked/waiver 不计入完成数。

文件级 Evidence 不能直接提升状态。发布器会为 79 个对象分别创建 lineage Artifact，并由一个
append-only 数据库 Migration Registry 原子绑定全部成员；只有该数据库 Registry 使用
`parity_passed`。

当前有效 Registry 为 `migration-registry.v0.22.3.json`。已提交的 `v0.22.0` 在全量 Catalog
相邻层和 Node Family 固定 stage 校验中发现 8 条 stage 错误后被取代。未发布的中间候选
不保存为完整重复 Registry；错误与修正规则记录在 M4 Catalog checkpoint。

`v0.22.1` 随后在兼容 runtime 接入时发现 recipe 不完整：离散信号错误继承连续信号的
tie policy，Factor 也未冻结 required price observations。`v0.22.2` 补齐实际执行策略；
身份映射、stage、Oracle binding 与既有 80 节点 Catalog 均未改变。

独立逐点比较随后发现 Signal recipe 还缺少候选资产作用域；Factor 包含 benchmark，Signal
只允许 `universe_member.role=candidate` 进入横截面排名。`v0.22.3` 冻结此边界，同样不改变
任何 Feature identity 或 stage。
