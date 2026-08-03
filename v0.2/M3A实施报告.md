# M3A 实施报告：Factor 核心结构与目录物化

## 交付结论

M3A 已把 M0 冻结的 12 个因子定义和 28 个参数实例从通用 JSON 目录物化为正式、类型化、不可变且可追溯的 Factor 业务对象。因子层只表达市场特征的测量方式，不保存方向、IC 或策略收益。

## 已交付

- 迁移 `20260803_07_v02_factor`；
- `factor_definition`、`factor_definition_version`、`factor_variant`；
- `factor_dataset`、`factor_value`、`factor_quality_issue` 核心边界；
- 公式、输入、implementation、参数哈希、精确历史需求和 preset type 的类型化约束；
- published 对象及 dataset 子行的数据库冻结；
- factor dataset 对 universe、bundle、eligibility 和 engine 的精确外键；
- 数据库门禁：eligibility 必须与 universe/bundle 一致且全部成员可用；
- `style-rotation factor bootstrap`；
- M0 目录内容哈希校验、幂等物化和新目录版本复用测试。

## 版本结构

```text
Factor Definition（稳定测量概念）
→ Definition Version（公式、输入、实现和时间语义）
→ Variant（参数实例与已解析历史需求）
→ Dataset（某个 variant × universe × bundle × eligibility × engine）
→ Value（asset × observation date × finite float64）
```

一个 factor dataset 只对应一个 variant。这样某个窗口或实现变化不会迫使所有参数实例共用一个大版本，后续 signal 也能引用准确的 variant 和实际 factor dataset。

## Catalog release 与对象版本分离

类型化 definition/version/variant 的身份不绑定整个 catalog release，因此未来 v0.2.1 增加新因子时，未变化的旧对象可以原样复用。系统另行发布 `factor_catalog_materialization` 聚合 artifact，将具体 catalog release 与全部物化成员连接起来。

物化前会重新计算本地 JSON 对应的 M0 artifact 内容哈希。同版本本地文件若被静默修改，操作会被拒绝。

## 下一阶段

M3B 将建立 versioned factor engine，读取指定 data bundle 和 eligibility，逐 variant 计算确定性 factor values，并发布独立 factor datasets。M3C 再增加分布、覆盖、相关性等因子本层诊断以及只读 API/双语页面。
