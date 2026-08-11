# M2B3A 实施报告：Canonical Dataset 与质量门

## 交付结论

M2B3A 已把不可变 source snapshot 转换为可供后续研究引用的 typed canonical publication。正式因子链路不再需要读取 wrapper CSV 或 FRED 原始文本，而是引用带清洗版本、日历版本、coverage、质量诊断和完整 artifact lineage 的确定数据集。

## 已交付

- 迁移 `20260803_05_v02_canonical_data`；
- `dataset_publication`、`dataset_input`、`daily_bar`、`corporate_action`；
- `rate_observation`、`dataset_coverage`、`quality_issue`；
- Yahoo wrapper CSV 与 FRED CSV 的纯解析、验证和固定精度量化；
- `Adj Close / raw Close` 同比例应用到当日 raw OHLC；
- observation date 与保守的 `+1 calendar day` availability 分离；
- `CanonicalDataPublicationService` 的 market/rate 原子、幂等、不可变发布；
- `style-rotation data publish-market` 与 `publish-rate`。

## 质量口径

阻止发布的 error 包括：缺失正式资产、重复行、必需值缺失、非正价格、OHLC 几何错误、负数或非整数成交量、负公司行动、非交易日观测和资产内部缺失 session。极端复权收益只形成 warning，不自动删值，也不阻止发布。

FRED 的 `.` 或空值形成 info 并保留 coverage 缺失计数；非法日期、重复 observation 或不合理利率形成 error。

## 确定性与血缘

- raw/adjusted prices 与公司行动在 hash 前量化至数据库 `numeric(24,10)` 精度；
- adjustment factor 量化至 `numeric(24,14)`，rate 量化至 `numeric(18,8)`；
- market publication 直接依赖五个 source snapshots、calendar version 和 cleaning version；
- rate publication 直接依赖 FRED snapshot 和 cleaning version；
- 发布后所有 publication、input、typed value、coverage 和 issue 行由数据库 trigger 冻结。

## 下一阶段

M2B3B 将从 canonical DGS3MO 生成 versioned reserve return dataset，并把 market、rate/reserve 和 calendar 组合为明确 data bundle；随后按 universe 和 data requirement set 发布逐资产 eligibility snapshot。
