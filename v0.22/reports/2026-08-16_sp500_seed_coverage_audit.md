# S&P 500 历史数据种子覆盖审计（2026-08-16）

状态：`BLOCKED_BEFORE_PUBLICATION`

本报告由只读审计生成。审计过程未下载数据、未连接或修改数据库，也未发布
Universe、Dataset 或 Evaluation Cohort。

## 候选实验环境

- `warmup_start`: 2004-12-31
- `evaluation_start`: 2007-01-03
- `evaluation_end`: 2026-06-30
- 最低暖机：504 个完整 XNYS sessions

这些日期仍是覆盖审计候选，不能因为数据不足而自动后移。

## 可复用的来源证据

- 历史成员源：2,718 个完整快照，覆盖 1996-01-02 至 2026-06-30；候选区间
  使用 1,704 个快照，成员数为 495–507。
- 候选区间共出现 974 个来源代码。
- 成员源 SHA-256：
  `39a9202c9ef69a74c0ff07e2113ad41fb6da7c8c5b6cd9541f0185fb4391e717`
- 来源许可证为 MIT；License SHA-256：
  `a0a71da320f7c856f189569ddb5c46a576cf02caae28ead6c40a27c0de006992`
- Source README SHA-256：
  `1b328209e11f1f02c27d1345cb078b0d147b47295fee36928cab59fef0b9df04`
- 冻结 Manifest 自身 SHA-256 与 FROZEN 声明一致：
  `65b628d604f7e2f456e8d1d43a3c3e88b6bd3e86cc1c9455cdcfe28b856a3ec7`

结论：成员源、许可证和说明文件可以作为 content-addressed 导入证据；不能据此
直接认定派生行情 Dataset 可进入正式排行榜。

## 阻塞项

1. **历史身份不完整**：974 个候选来源代码中，774 个已有映射，200 个尚无稳定
   Security 映射。不得按代码字符串自动合并、删除或映射到现存公司。
   已生成的确定性审查队列显示：200 个代码均为历史项，最后观察日不晚于
   2014-06-19；其中 180 个在 2013-01-02 前已离开成员集。199 个代码只有
   一段连续成员期，1 个有两段成员期。这些统计只用于排定审查顺序，不是
   Security 连续性证据。
2. **候选暖机行情缺失**：冻结价格仅覆盖 2013-01-02 至 2026-06-30，而候选
   `warmup_start` 为 2004-12-31。
3. **供应商不可用**：现有 745 个稳定 Security 中，610 个来自 Yahoo、71 个来自
   Tiingo、64 个标记 unavailable。后者只有在身份和排除证据明确、并对所有策略统一
   排除时，才可能进入 `rankable_research`。
4. **冻结构建字节漂移**：Manifest 的 62 个对象都存在，但仅 56 个 size/hash 完全
   匹配。6 个不匹配对象均为 builder source（两份脚本、pyproject 及三份 Python
   源码）。原始数据与质量证据未因此被判定丢失，但现存派生 Dataset 不能冒充原冻结
   builder 的可重放产物。
5. **来源等级明确受限**：现有冻结种子声明 `formal_eligible=false`，只能作为重建
   输入与修复台账，不能直接发布为主排行榜 Dataset。

## 决定与下一步

- 当前禁止发布正式 S&P 500 Universe、Dataset、Evaluation Cohort 和排行榜。
- 先发布/登记可验证的成员源、License、README、身份修复和原始 provider 对象证据；
  漂移的 builder source 不作为 verified 对象。
- 为 200 个未映射代码建立逐项身份审查队列；纯改名、ticker reuse、并购、换股、破产
  和 share-class conversion 必须分别处理。
- 历史成员映射已改为半开生效区间 `[valid_from, valid_to)`：同一 ticker 只有在
  声明区间内才能解析为指定 Security；区间重叠、空洞或未使用映射都会阻止
  Universe 发布。这允许声明式处理 ticker reuse，而不把两家公司合并。
- 身份稳定后，补齐 2004-12-31 至 2012-12-31 的行情缺口，并在 v0.22 中重新构建
  完整 Dataset；不得直接复制最终 Parquet 作为新权威身份。
- 完成 terminal event、公司行动、停牌/provider gap 和统一排除 QA 后，再提交候选日期
  的最终覆盖报告给用户确认。

可复现只读命令入口：
`python -m style_rotation.cli.v022_sp500_data <runtime_root> <source_project_root>`。
加上 `--include-identity-review` 可输出逐项未解析队列；该选项不写入数据库，
也不自动推断身份。
