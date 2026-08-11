# M7B 实施报告：Net Cost Path

## 交付结果

M7B 在不改变 Gross Portfolio Path 的前提下，实现正式 2/5/10 bps 线性成本场景和不可变 Net Cost Path。每个成本场景拥有独立净 NAV 与实际美元成本，但共享目标仓位、标准化权重、交易方向、执行价和 gross traded fraction。

## 会计口径

每次执行先由该 Net Path 的开盘前净 NAV 乘以 Gross Path 的标准化资产交易比例，得到实际成交名义金额；再按逐边 bps 计算成本。成本在开盘扣除，剩余净值继续参与当日日内收益，因此早期成本也会产生后续复利拖累。

储备是 synthetic reserve sleeve，不是交易证券，其权重变化不重复收费。首次从 100% reserve 建仓会产生真实资产买入成本；期末不强制卖出，也不添加虚构成本。没有资产交易时成本为零。

## 数据与追溯

- Cost Model Definition/Version 保存收费基础、扣费时点、reserve 规则和 bps divisor；
- Cost Scenario 固定正式 2/5/10 bps；
- Net Cost Path 绑定一条已发布 Gross Path 和一个已发布 Cost Scenario；
- Net Daily NAV 保存 net return、net NAV、同日 gross NAV 和每日成本；
- Execution Cost 保存净路径执行前 NAV、实际成交名义金额、成本比例和成本金额；
- 发布后所有成本目录、路径和明细均不可修改。

## 验证结果

- 172 个单元测试通过；
- Ruff 与 strict Mypy 通过；
- PostgreSQL migration 从空库升级和降级回归通过；
- 端到端链验证三种成本场景共享同一 Gross Path、逐笔成本对账、发布幂等性和净路径不可变；
- 在相同 Gross Path 下，最终净值满足 2 bps > 5 bps > 10 bps，且均不高于 gross NAV。

下一阶段 M7C 将建立产品与研究 benchmark paths。
