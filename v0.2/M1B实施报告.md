# v0.2 M1B实施报告：数据库基础

## 1. 阶段状态

M1B已完成。v0.1的六个Alembic revision和对应集成测试已由一条干净v0.2基线替换。Git历史仍可恢复旧实现。

## 2. 已完成内容

- 创建`catalog/data/factor/signal/model/strategy/experiment/lineage/ops`九个schema；
- 创建`lineage.artifact`、dependency和status event基础表；
- 创建Ops engine definition/version、run attempt/event/error/artifact和quality check表；
- 使用应用生成UUID、带时区时间、text CHECK、SHA-256格式检查、唯一约束和`ON DELETE RESTRICT`；
- Alembic只加载v0.2 Lineage/Ops ORM，不再加载v0.1巨型模型；
- 程序传入的数据库URL优先于环境默认值，保证测试和CLI连接正确目标；
- 实现`style-rotation db status/upgrade/reset`；
- reset限定localhost、local/test、项目数据库名和精确名称确认；
- 增加独立`postgres-test`服务，使用localhost:55432和独立volume；
- 增加空库迁移、约束、降级/升级和安全目标测试。

其他七个业务schema当前有意保持为空。它们只建立边界，不提前创建M2–M7业务表。

## 3. 审计与实施中发现的问题

### 测试URL被Alembic环境覆盖

初次集成测试发现旧`migrations/env.py`会用默认URL覆盖程序显式传入的测试URL。现已改为显式override优先，避免迁移误连错误数据库。

### 测试端口冲突

5433已由另一个项目的健康PostgreSQL容器使用。未停止或修改该容器；本项目测试数据库改为55432。

### ORM与迁移约束名称漂移

真实迁移通过后，`alembic check`发现若干唯一约束名称和一个索引只存在于迁移。ORM已同步，最终检查为`No new upgrade operations detected`。

## 4. 验证结果

```text
Ruff: passed
Mypy --strict: passed
Unit tests: 63 passed
PostgreSQL integration tests: 4 passed
Alembic clean upgrade: passed
Alembic downgrade base → upgrade head: passed
Alembic ORM drift check: passed
style-rotation db reset/status: passed
Machine catalog validation: passed
```

FastAPI旧测试仍产生一个已知弃用警告，留给M1D处理。

## 5. 尚未包含

M1B没有实现artifact发布事务、发布后冻结、manifest、invalidation/taint传播、canonical serialization v2和并发幂等。这些属于M1C。

M1B也没有创建资产、数据、因子、信号、模型、策略或实验业务表。

## 6. 下一阶段M1C

M1C将实现：

1. canonical serialization v2和数值环境身份；
2. semantic fingerprint与content hash；
3. draft→published原子事务和数据库冻结保护；
4. artifact dependency无环、manifest和状态事件；
5. invalidated→tainted传播；
6. fingerprint并发幂等；
7. `bootstrap/lineage/artifact`CLI的首批可用操作。
