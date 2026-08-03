# M2A 实施报告：资产目录与研究范围

## 交付结论

M2A 已将资产、上市信息、分类、研究宇宙和数据需求从设计文档落入正式数据库、发布流程、只读 API 和双语前端。它只回答“研究哪些对象、这些对象需要什么数据”，不提前抓取市场数据，也不展示策略表现。

## 已交付

- `research_scope.v0.2.0.json`：研究范围的唯一机器可读输入；
- `20260802_03_v02_catalog`：Catalog schema 的首个正式迁移；
- 五个稳定资产身份：IWF、IWD、IWO、IWN、SPY；
- 一个研究宇宙：四个 candidate 和一个 product benchmark；
- 一个数据需求版本：价格、公司行动、SPY、DGS3MO 和 XNYS 日历；
- `style-rotation bootstrap scope`：原子发布 master data、universe 和 requirement artifacts；
- `/api/v2/catalog/assets` 与 `/api/v2/catalog/data-requirements`；
- Assets 双语页面及桌面/移动布局。

## 关键语义

1. 资产身份不等于 ticker。Ticker 存在 `listing_symbol`，以后可以带有效期变化。
2. 不建立强制 Market 层。交易场所、币种、时区和交易日历属于 listing。
3. SPY 是真实可交易资产，也是产品主基准。
4. DGS3MO 是参考利率序列，不是资产，也不进入 universe。
5. Universe membership 和 data requirement 都是独立、不可变、可追溯的发布物。
6. 三个目录发布物共享一个外层数据库事务；任一写入失败时整组回滚。
7. Published 后，数据库触发器阻止目录业务行的 INSERT、UPDATE 和 DELETE。

## 暂缓到 M2B

- XNYS 逐日 session 数据和版本；
- Yahoo/FRED source snapshot；
- raw、canonical 和 derived dataset；
- coverage、quality issue 和 eligibility snapshot；
- 实际数据抓取、清洗与冻结。

## 验证

- Pydantic 校验引用、唯一性、连续 ordinal、候选/基准角色和字段集合；
- PostgreSQL 全量迁移、降级与 schema drift 检查；
- 发布幂等性、目录冻结、API 真实数据库集成测试；
- Python Ruff、Mypy 和 Pytest；
- 前端 TypeScript、ESLint、Vitest 和生产构建。
