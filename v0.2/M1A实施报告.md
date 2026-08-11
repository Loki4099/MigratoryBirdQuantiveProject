# v0.2 M1A实施报告：工程基线

## 1. 阶段状态

M1A已完成。项目元数据和统一入口已切换到v0.2.0；数据库DDL、迁移和金融计算尚未开始重建。

## 2. 已完成内容

- 项目和Python package版本更新为`0.2.0`；
- `.env.example`移除单一`public` schema假设；
- 建立Catalog、Factor、Signal、Model、Strategy、Experiment、Lineage和Ops单数包；Data包将在M2替换；
- 建立可自动验证的九领域依赖清单；
- 旧的多个console script替换为统一`style-rotation`入口；
- 实现`style-rotation --version`和`style-rotation modules`；
- 预注册`db/bootstrap/data/factor/signal/model/strategy/experiment/lineage/artifact/backup/api`；
- 未完成命令明确返回exit code 2并标明交付里程碑；
- 根README更新为v0.2入口，不再指导用户运行v0.1的288次流水线。

## 3. 过渡边界

旧`factors`、`signals`、`backtest`、`metrics`、`persistence`和`web`代码暂时保留，用于参考和确认M1A没有误伤现有工作区。它们不再拥有正式console script入口，也不代表v0.2架构。

后续纵向阶段完成对应能力后删除旧模块，不建立长期v0.1/v0.2双运行兼容层。Git历史继续承担v0.1保存责任。

现有Alembic迁移仍是v0.1结构。M1B完成以前不得把它当作v0.2数据库基线；统一`style-rotation db`当前会明确拒绝执行。

## 4. 验证结果

```text
Ruff: passed
Mypy --strict: passed
Unit tests: 59 passed
Catalog validation: passed
```

测试环境仍报告一个来自既有FastAPI TestClient/httpx组合的弃用警告，不是M1A引入的运行错误。M1D重建API测试依赖时处理。

## 5. M1B范围

M1B只负责数据库基础：

1. 移除v0.1 Alembic revisions并建立干净v0.2基线；
2. 创建九个PostgreSQL schema；
3. 实现最基础的identity、artifact、engine、run和status表；
4. 建立UUID、UTC时间、状态CHECK、外键RESTRICT和必要索引；
5. 实现安全的`style-rotation db status/upgrade/reset`边界；
6. 使用真实PostgreSQL完成空库迁移、约束和降级/重建测试。

M1B不创建Catalog/Data/Factor等完整业务表，也不实现发布事务和hash；这些分别属于M2及M1C。
