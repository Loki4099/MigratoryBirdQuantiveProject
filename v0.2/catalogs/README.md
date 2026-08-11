# v0.2.0机器可读研究目录

本目录是M0冻结的bootstrap输入和数量验收来源。自然语言文档解释设计理由；这里的JSON决定首批对象key、参数、生成规则和预期数量。实现不得在代码中维护另一份不同的隐藏目录。

## 文件

- `factors.v0.2.0.json`：12个factor definition、28个factor variant及明确公式/输入/历史需求；
- `signals.v0.2.0.json`：27个signal template，展开为51个signal；
- `models.v0.2.0.json`：5个代表性维度、31个非空维度子集模式和模型生成规则；
- `strategies.v0.2.0.json`：3个策略模板、K=1/2/3、周/月频及兼容性策略。
- `forward_returns.v0.2.0.json`：周频/月频两种并行的下一执行开盘到下一执行开盘评价目标。

运行：

```powershell
.\.venv\Scripts\python.exe v0.2/tools/validate_catalogs.py
```

当前期望输出：

| 项目 | 数量 |
| --- | ---: |
| Factor definitions | 12 |
| Factor variants | 28 |
| Signal templates | 27 |
| Generated signals | 51 |
| Product-eligible signals | 41 |
| Representative dimensions | 5 |
| Dimension subset patterns | 31 |
| Concrete model specifications | 86 |
| Strategy variant configurations | 9 |
| Schedule versions | 2 |
| Forward-return targets | 2 |

## 数量含义

31是五个维度的非空组合模式，不是全部模型总数。86个具体模型由以下部分组成：

```text
51 single-signal
+ 31 representative dimension-subset equal-weight
+ 2 named fixed-weight
+ 2 directional vote
= 86
```

策略的9个配置是3个规则模板×3个K值。周/月频属于strategy product，因而每个配置有2个schedule选择。系统不在bootstrap时自动创建模型、策略、频率、成本和区间的完整笛卡尔积；Experiment Suite按明确请求展开并永久保存实际候选全集。

## 变更规则

已发布目录内容不原地修改。修正公式、参数、方向或生成规则时：

1. 创建新的catalog version；
2. 保留旧JSON；
3. 更新validator中的对应期望或从新目录元数据读取；
4. 由bootstrap生成新的definition/version/specification；
5. 不覆盖旧研究结果。
