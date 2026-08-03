# M2B3B 实施报告：Reserve、Data Bundle 与 Eligibility

## 交付结论

M2B3B 已完成正式研究数据入口。系统现在可以把 canonical DGS3MO 转换为可追溯的现金收益代理，将 market、rate、reserve 和 calendar 固定为一个不可变 data bundle，并针对具体 universe、requirement、区间和暖机要求发布逐资产 eligibility snapshot。

## 已交付

- 迁移 `20260803_06_v02_bundle`；
- versioned reserve return model definition/version；
- `reserve_model.v0.2.0.json` 机器目录；
- `reserve_return` typed intervals；
- `data_bundle_definition/version/member`；
- `eligibility_snapshot/item/issue`；
- reserve、bundle 与 eligibility CLI；
- published 子对象数据库冻结和完整 artifact dependencies。

## Reserve 口径

区间收益因子为：

```text
1 + (annual_rate_percent / 100) × (actual_calendar_days / 365)
```

每个区间使用 interval start 时已公开可得的最新 DGS3MO。相邻 XNYS sessions 之间按实际日历天数计提，因此周五至周一为三天。陈旧 0–5 日 normal，6–10 日 warning，超过 10 日 error 并阻止 derived dataset 发布。负利率不自动截断，只要求最终 accrual factor 为正。

## Bundle 口径

首个 bundle 固定四个唯一角色：canonical market、canonical rate、reserve return 和 trading calendar。共同可用区间由 market、reserve 和 calendar 相交；canonical rate 是 reserve 的可追溯上游，其 observation date 尾端不错误缩短已经由 availability 规则解析出的 reserve coverage。

## Eligibility 口径

Eligibility 不修改 universe membership。每个 universe member 都保留一行，并记录可用首尾日期、观测数、data-ready date、是否合格及所有原因。

当前 CLI 默认 `warmup_observations=253`，对应首批因子目录的最长价格历史需求；该值进入 semantic fingerprint，可以显式覆盖。未来 comparison cohort 将沿完整依赖图解析暖机值，再把解析结果传给同一 eligibility 发布接口。

不足暖机、请求区间缺 session、bundle/reserve 覆盖不足都会使资产不合格。诊断快照仍然发布，便于解释失败；不合格 snapshot 不能被正式因子或实验链路使用。

## 下一阶段

M2B4 将提供只读 Data API 和双语数据质量页面，展示 source、canonical、coverage、issues、bundle 与 eligibility，不在前端重新计算数据资格。
