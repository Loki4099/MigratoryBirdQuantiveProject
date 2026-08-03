# M3B 实施报告：确定性因子计算与发布

## 交付结论

M3B 已打通正式 Factor 数据链路。系统读取一个明确发布的数据包、全员通过的 eligibility 快照、Factor 目录物化版本和 Factor Engine 版本，先在内存中完成全部 28 个参数实例的计算，再在一个数据库事务中逐项发布 28 个独立数据集。任一计算或校验失败时，不会留下部分正式结果。

## 已交付

- 12 个资产无关的 Factor 计算实现，覆盖 28 个首批参数实例；
- 统一使用已复权价格计算价格类测量，流动性类明确使用 raw close 与 raw volume；
- 按 `asset_key → session_date` 固定归约顺序并以 IEEE 754 float64 内容参与哈希；
- 精确读取 eligibility 的统一有效区间及其之前的暖机数据；
- 强制 eligibility 与 bundle 一致、全体 universe 成员可用、暖机不少于目录最大需求；
- Factor Engine 记录语义版本、Git commit、依赖锁哈希、数据库 revision、配置哈希和数值环境；
- migration `20260803_08_v02_factor_engine` 冻结 engine definition/version；
- 每个 Factor dataset 精确依赖 variant、universe、bundle、eligibility 和 engine 五个上游制品；
- `style-rotation factor bootstrap-engine` 与 `style-rotation factor publish`；
- 公式 golden tests、命令行测试、迁移测试及 28 数据集端到端集成测试。

## 发布身份

```text
Factor Variant
× Universe Version
× Data Bundle
× Eligibility Snapshot / Effective Range
× Factor Engine Version
→ one immutable Factor Dataset
```

Factor catalog release 不直接进入 dataset 身份；dataset 依赖的是具体且不可变的 variant。未来 v0.2.1 只扩充目录而不修改旧 variant 时，旧数据集可以安全复用。

## 原子性与复用

所有 variant 在打开发布事务前先完成计算和完整性检查。只有全部结果均覆盖相同请求日期、资产集合完整且数值有限时才进入发布。相同五项身份和相同内容再次运行会返回原 artifact，不会生成重复记录；不同 bundle、eligibility、engine 或 variant 会自然产生新身份。

## 下一阶段

M3C 将只消费已发布 Factor datasets，增加覆盖、分布、质量、参数稳定性和因子值相关性诊断，并实现只读 Factors API 与中英双语页面。Rank IC、正 IC 比例和 Top-Bottom 仍属于有方向的 Signal 层。
